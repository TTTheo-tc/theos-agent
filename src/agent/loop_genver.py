"""GenVer handler for AgentLoop (composition object).

Owns GenVer detection, loop execution, handoff tracking, and user interaction
during the Generator-Verifier cycle.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

if TYPE_CHECKING:
    from src.agent.loop_memory import MemoryHandler
    from src.agent.subagent import SubagentManager
    from src.agent.tools.registry import ToolRegistry
    from src.bus.queue import MessageBus
    from src.providers.base import LLMProvider
    from src.session.turn_store import TurnStore


class GenVerHandler:
    """Encapsulates GenVer-specific state and operations for AgentLoop."""

    def __init__(
        self,
        provider: "LLMProvider",
        workspace: Path,
        bus: "MessageBus",
        turn_store: "TurnStore | None" = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self._turn_store = turn_store
        self._active_workspaces: dict[str, Path] = {}
        self._pending_questions: dict[str, asyncio.Future[str]] = {}
        self._pending_turn_ids: dict[str, str] = {}
        self._last_handoffs: dict[str, Any] = {}

    # -- narrow public APIs for run() in AgentLoop ----------------------------

    def get_pending_question(self, session_key: str) -> asyncio.Future[str] | None:
        """Return the pending genver question Future for *session_key*, or None."""
        return self._pending_questions.get(session_key)

    def clear_pending_question(self, session_key: str) -> None:
        """Remove the pending genver question for *session_key* if it exists."""
        self._pending_questions.pop(session_key, None)
        self._pending_turn_ids.pop(session_key, None)

    def get_pending_turn_id(self, session_key: str) -> str | None:
        """Return the turn id for a pending user question, if any."""
        return self._pending_turn_ids.get(session_key)

    def get_active_workspace(self, session_key: str) -> Path | None:
        """Return the active genver workspace for *session_key*, or None."""
        return self._active_workspaces.get(session_key)

    # -- genver request detection (static) ------------------------------------

    @staticmethod
    def should_run_for_request(user_request: str) -> bool:
        """Return True only for requests that look like software build/change tasks."""
        text = (user_request or "").strip().lower()
        if not text:
            return False

        code_markers = (
            "build",
            "create",
            "implement",
            "fix",
            "refactor",
            "write code",
            "bug",
            "api",
            "backend",
            "frontend",
            "dashboard",
            "cli",
            "script",
            "repo",
            "github",
            "gitlab",
            "代码",
            "项目",
            "仓库",
            "脚本",
            "接口",
            "前端",
            "后端",
            "服务",
            "测试",
            "函数",
            "类",
            "模块",
            "工具",
            "系统",
            "平台",
            "应用",
            "网页",
            "网站",
            "修复",
            "重构",
            "开发",
            "实现",
            "编写",
            "搭建",
            "建立",
            "新增",
            "修改",
        )
        if any(marker in text for marker in code_markers):
            return True

        return bool(
            re.search(
                r"[/\\]|github\.com|gitlab\.com|\.py\b|\.js\b|\.ts\b|\.tsx\b|\.jsx\b|readme\.md|pyproject\.toml|package\.json",
                text,
                flags=re.I,
            )
        )

    # -- genver loop execution ------------------------------------------------

    async def run_loop(
        self,
        initial_messages: list[dict],
        *,
        tools: "ToolRegistry",
        model: str,
        temperature: float,
        max_tokens: int | None,
        restrict_to_workspace: bool,
        exec_config: Any,
        brave_api_key: str | None,
        web_search_max_results: int,
        web_search_provider: str | None,
        tavily_api_key: str | None,
        orchestrator_config: Any,
        cron_service: Any,
        stock_config: Any,
        provider_keys: dict[str, str] | None,
        channel_env: dict[str, str],
        memory_handler: "MemoryHandler",
        genver_config: Any,
        context_add_assistant: Callable,
        context_add_tool_result: Callable,
        subagent_manager: "SubagentManager | None" = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        tool_context: Any = None,
        session_key: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
        """Run the Generator-Verifier loop instead of a plain tool loop."""
        from src.genver.runner import prepare_genver_tools
        from src.genver.workspace import resolve_task_workspace

        config = genver_config
        user_request = ""
        for msg in reversed(initial_messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                user_request = msg["content"]
                break

        task_workspace = resolve_task_workspace(self.workspace, user_request)
        if session_key:
            self._active_workspaces[session_key] = task_workspace

        ns_config = orchestrator_config.neuro_symbolic if orchestrator_config else None
        generator_tools = prepare_genver_tools(
            config=config,
            base_tools=tools,
            workspace=self.workspace,
            task_workspace=task_workspace,
            provider=self.provider,
            default_model=model,
            restrict_to_workspace=restrict_to_workspace,
            exec_config=exec_config,
            brave_api_key=brave_api_key,
            web_search_max_results=web_search_max_results,
            web_search_provider=web_search_provider,
            tavily_api_key=tavily_api_key,
            neuro_symbolic_config=ns_config,
            cron_service=cron_service,
            memory_index_resolver=memory_handler.resolve_index_for_tools,
            memory_search_enabled=memory_handler.search_enabled(),
            memory_search_max_results=memory_handler.search_max_results(),
            memory_search_min_score=memory_handler.search_min_score(),
            structured_workspace_resolver=lambda sk: memory_handler.resolve_structured_workspace_for_tools(
                sk,
                genver_workspace_resolver=lambda k: self._active_workspaces.get(k),
            ),
            stock_config=stock_config,
            provider_keys=provider_keys,
            channel_env=channel_env,
            subagent_manager=subagent_manager,
        )

        _ask_fn = None
        if self.bus and session_key:

            async def _pipeline_ask(question: str) -> str | None:
                return await self.ask_user(
                    question,
                    channel=channel_env.get("channel_name", "")
                    or getattr(tool_context, "channel", "cli")
                    or "cli",
                    chat_id=channel_env.get("chat_id", "")
                    or getattr(tool_context, "chat_id", "direct")
                    or "direct",
                    session_key=session_key,
                    turn_id=turn_id,
                )

            _ask_fn = _pipeline_ask

        from src.genver.pipeline import GenVerPipeline

        pipeline = GenVerPipeline(
            config=config,
            provider=self.provider,
            workspace=task_workspace,
            generator_tools=generator_tools,
            default_model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            add_assistant=context_add_assistant,
            add_tool_result=context_add_tool_result,
            on_progress=on_progress,
            tool_context=tool_context,
            ask_user=_ask_fn,
        )
        try:
            result = await pipeline.run(initial_messages)
            # Surface handoff for orchestrator review_mode=auto, keyed by session.
            if session_key and pipeline.last_handoff is not None:
                self._last_handoffs[session_key] = pipeline.last_handoff
            return result
        finally:
            if session_key:
                self._active_workspaces.pop(session_key, None)

    # -- handoff management ---------------------------------------------------

    def pop_handoff(self, session_key: str) -> Any | None:
        """Consume and return the latest GenVer handoff for the given session."""
        return self._last_handoffs.pop(session_key, None)

    # -- user interaction during genver ---------------------------------------

    async def ask_user(
        self,
        question: str,
        *,
        channel: str,
        chat_id: str,
        session_key: str | None,
        turn_id: str | None = None,
    ) -> str | None:
        """Ask the user a question during genver loop and wait for the same session's reply."""
        from src.bus.events import OutboundMessage

        if not session_key:
            return "abort"

        answer_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending_questions[session_key] = answer_future
        if turn_id:
            self._pending_turn_ids[session_key] = turn_id
            if self._turn_store is not None:
                self._turn_store.record(
                    session_key,
                    turn_id,
                    "waiting_user",
                    question=question,
                    timeout_seconds=300,
                )

        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=question,
                metadata={"_genver_ask": True},
            )
        )

        try:
            return await asyncio.wait_for(answer_future, timeout=300)
        except asyncio.TimeoutError:
            logger.warning("[GenVer] User did not respond within 5 minutes, aborting")
            return "abort"
        finally:
            current = self._pending_questions.get(session_key)
            if current is answer_future:
                self._pending_questions.pop(session_key, None)
                self._pending_turn_ids.pop(session_key, None)
