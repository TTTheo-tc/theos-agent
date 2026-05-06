"""Turn finalization for AgentLoop (composition object).

Owns post-LLM processing: outbound safety scan, session save, structured
memory persistence, dashboard updates, hooks, and response construction.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from src.agent.context import ContextBuilder
from src.agent.loop_context import _EPHEMERAL_CONTEXT_TAG
from src.agent.tools.message import MessageTool
from src.bus.events import OutboundMessage
from src.session.manager import Session
from src.utils.truncation import truncate_tool_call_arguments

if TYPE_CHECKING:
    from src.bus.events import InboundMessage
    from src.hooks.runner import HookRunner
    from src.memory.tiers import MemoryTierManager
    from src.providers.base import LLMProvider
    from src.safety.layer import SafetyLayer
    from src.session.manager import SessionManager


class TurnFinalizer:
    """Encapsulates post-LLM turn finalization for AgentLoop."""

    _TOOL_RESULT_MAX_CHARS = 3000
    _TOOL_CALL_ARGS_MAX_CHARS = 1200
    _TASK_FAILURE_MARKERS = (
        "without completing the task",
        "have no response to give",
    )

    def __init__(
        self,
        hooks: "HookRunner",
        safety_fn: Callable[[], "SafetyLayer"],
        sessions: "SessionManager",
        provider: "LLMProvider | None" = None,
    ):
        self.hooks = hooks
        self._get_safety = safety_fn
        self.sessions = sessions
        self._provider = provider

    # -- static / classmethod helpers -----------------------------------------

    @staticmethod
    def classify_task_outcome(response: str | None) -> tuple[str, str | None]:
        """Return (status, error_text) for a completed agent turn."""
        text = (response or "").strip()
        if not text:
            return "failed", "Empty response"
        if text.startswith("Error") or text.startswith("\u26a0"):
            return "failed", text
        if any(marker in text for marker in TurnFinalizer._TASK_FAILURE_MARKERS):
            return "failed", text
        return "success", None

    @staticmethod
    def rewrite_invalid_request_error(
        response: str | None, usage: dict[str, int] | None
    ) -> str | None:
        """Convert provider-side invalid request failures into a short recovery hint."""
        reason = TurnFinalizer._invalid_request_reason(response, usage)
        if reason is None:
            return response
        logger.error("LLM invalid_request_error (prompt_tokens=0): {}", response)
        if reason == "tool_schema":
            tool_name = TurnFinalizer._extract_invalid_tool_name(response)
            if tool_name:
                return (
                    "当前请求被模型提供商拒绝，原因是工具 "
                    f"`{tool_name}` 的参数 schema 无效，导致请求无法发送。"
                    "这不是会话损坏，需要修复工具定义后再试。"
                )
            return (
                "当前请求被模型提供商拒绝，原因是某个工具的参数 schema 无效，"
                "导致请求无法发送。这不是会话损坏，需要修复工具定义后再试。"
            )
        return (
            "当前请求被模型提供商拒绝，原因是会话上下文已损坏或格式无效。"
            "请发送 `/new` 开启一个新会话后再重试。"
        )

    @staticmethod
    def _is_invalid_request_error(response: str | None, usage: dict[str, int] | None) -> bool:
        text = (response or "").strip().lower()
        if not text:
            return False
        if (usage or {}).get("prompt_tokens", 0) != 0:
            return False
        return "invalid_request_error" in text

    @staticmethod
    def _invalid_request_reason(response: str | None, usage: dict[str, int] | None) -> str | None:
        if not TurnFinalizer._is_invalid_request_error(response, usage):
            return None
        text = (response or "").lower()
        if "invalid_function_parameters" in text or ("tools[" in text and "schema" in text):
            return "tool_schema"
        return "generic"

    @staticmethod
    def _extract_invalid_tool_name(response: str | None) -> str | None:
        text = response or ""
        match = re.search(r"function '([^']+)'", text)
        return match.group(1) if match else None

    @staticmethod
    def append_routing_footer(
        content: str,
        routing_domains: list[str],
        routed_skills: list[str],
    ) -> str:
        """Append a compact routing footer showing matched domains and skills."""
        if not routing_domains and not routed_skills:
            return content
        parts: list[str] = []
        if routing_domains:
            parts.append(f"domain: {', '.join(routing_domains)}")
        if routed_skills:
            parts.append(f"skills: {', '.join(routed_skills)}")
        footer = " | ".join(parts)
        return f"{content}\n\n---\n\U0001f4a1 {footer}"

    # -- turn finalization ----------------------------------------------------

    async def finalize_turn(
        self,
        msg: "InboundMessage",
        *,
        key: str,
        session: Session,
        final_content: str | None,
        tools_used: list[str],
        all_msgs: list[dict],
        initial_count: int,
        usage: dict[str, int] | None,
        run_genver: bool,
        task_workspace: Path,
        routing_domains: list[str],
        selected_primary: str | None,
        routed_skills: list[str],
        agent_id: str,
        t0: float,
        dashboard: Any | None,
        memory: Any,
        model: str,
        bus: Any,
        genver_last_handoff: Any | None,
        tools: Any,
        workspace: Path,
        memory_tiers: "MemoryTierManager | None",
        turn_id: str | None = None,
        persisted_user_message: bool = False,
    ) -> OutboundMessage | None:
        """Post-LLM processing: safety scan, save, hooks, and response."""
        import time as _time

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        final_content, task_status, task_error = self._prepare_final_content(
            final_content,
            usage=usage,
        )
        genver_handoff, post_chat_artifacts, post_chat_tests = self._genver_post_chat_context(
            run_genver=run_genver,
            genver_last_handoff=genver_last_handoff,
        )

        if all_msgs and all_msgs[-1].get("role") == "assistant":
            all_msgs[-1] = {**all_msgs[-1], "content": final_content}

        self.save_turn(
            session,
            all_msgs,
            initial_count,
            usage=usage,
            user_message=msg.content,
            memory_tiers=memory_tiers,
            turn_id=turn_id,
            persisted_user_message=persisted_user_message,
        )
        self.sessions.save(session)
        _duration_ms = (_time.monotonic() - t0) * 1000
        await memory.persist_structured_memory(
            session_key=key,
            user_message=msg.content,
            response=final_content,
            tools_used=tools_used,
            routed_skills=routed_skills,
            routing_domains=routing_domains,
            selected_primary=selected_primary,
            usage=usage,
            duration_ms=_duration_ms,
            artifacts=post_chat_artifacts,
            tests=post_chat_tests,
            status=task_status,
            workspace_override=task_workspace if run_genver else None,
        )

        # Background memory extraction: pull durable facts from the turn
        # into MEMORY.md.  Fire-and-forget — failures never crash the loop.
        unconsolidated_count = len(session.messages) - session.last_consolidated
        if (
            final_content is not None
            and not run_genver
            and unconsolidated_count >= 2
            and self._provider is not None
        ):
            asyncio.ensure_future(self._background_extract(session, memory=memory, model=model))

        # Dashboard writes — TURN END (Phase 2 of 2)
        # These fire-and-forget writes record completion state.
        # Phase 1 (turn-start writes) is in AgentLoop._init_session().
        # Do not consolidate both phases into one location — the two-phase
        # timing is intentional for real-time dashboard visibility.
        if dashboard:
            self._schedule_dashboard_finish(
                dashboard,
                key=key,
                channel=msg.channel,
                agent_id=agent_id,
                usage=usage,
                duration_ms=_duration_ms,
                message_count=len(session.messages),
            )

        # Post-chat hook: fire-and-forget. The reflector_active payload field is
        # kept for post-chat script compatibility; reflect.js ignores it.
        self._schedule_post_chat(
            key,
            response=final_content,
            error=task_error,
            status=task_status,
            user_message=msg.content,
            tools_used=tools_used,
            usage=usage,
            duration_ms=_duration_ms,
            routing_domains=routing_domains,
            selected_primary=selected_primary,
            workspace=task_workspace if run_genver else workspace,
            artifacts=post_chat_artifacts,
            tests=post_chat_tests,
        )

        if self._message_tool_sent_in_turn(tools):
            return None

        # Append routing footer (domain + skills) to the response
        final_content = self.append_routing_footer(final_content, routing_domains, routed_skills)

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=self._outbound_metadata(msg, usage=usage, genver_handoff=genver_handoff),
        )

    def _prepare_final_content(
        self,
        final_content: str,
        *,
        usage: dict[str, int] | None,
    ) -> tuple[str, str, str | None]:
        """Apply outbound safety scan and provider-error rewriting."""
        safety_result = self._get_safety().scan_outbound(final_content)
        scanned_content = safety_result.output_text
        task_status, task_error = self.classify_task_outcome(scanned_content)
        rewritten_content = self.rewrite_invalid_request_error(scanned_content, usage)
        if rewritten_content != scanned_content:
            return rewritten_content or scanned_content, "failed", scanned_content
        return scanned_content, task_status, task_error

    def _genver_post_chat_context(
        self,
        *,
        run_genver: bool,
        genver_last_handoff: Any | None,
    ) -> tuple[Any | None, list[str], list[str]]:
        genver_handoff = genver_last_handoff if run_genver else None
        artifacts = list(dict.fromkeys(getattr(genver_handoff, "files_changed", []) or []))
        tests = [
            path
            for path in artifacts
            if isinstance(path, str) and path.lstrip("./").startswith("tests/")
        ]
        return genver_handoff, artifacts, tests

    def _schedule_dashboard_finish(
        self,
        dashboard: Any,
        *,
        key: str,
        channel: str,
        agent_id: str,
        usage: dict[str, int] | None,
        duration_ms: float,
        message_count: int,
    ) -> None:
        asyncio.ensure_future(dashboard.finish_agent(agent_id, usage=usage, duration_ms=duration_ms))
        total_tokens = (usage or {}).get("input_tokens", 0) + (usage or {}).get("output_tokens", 0)
        asyncio.ensure_future(
            dashboard.upsert_session(
                key,
                channel,
                message_count=message_count,
                total_tokens=total_tokens,
            )
        )
        asyncio.ensure_future(dashboard.emit_event(key, "agent_finished", agent_id=agent_id))

    def _schedule_post_chat(
        self,
        key: str,
        *,
        response: str,
        error: str | None,
        status: str,
        user_message: str,
        tools_used: list[str],
        usage: dict[str, int] | None,
        duration_ms: float,
        routing_domains: list[str],
        selected_primary: str | None,
        workspace: Path,
        artifacts: list[str],
        tests: list[str],
    ) -> None:
        asyncio.create_task(
            self.hooks.run_post_chat(
                key,
                response=response,
                error=error,
                status=status,
                user_message=user_message,
                tools_used=tools_used,
                usage=usage,
                duration_ms=duration_ms,
                routing_domains=routing_domains,
                selected_primary=selected_primary,
                workspace=workspace,
                reflector_active=False,
                artifacts=artifacts,
                tests=tests,
            )
        )

    def _message_tool_sent_in_turn(self, tools: Any) -> bool:
        mt = tools.get("message")
        return isinstance(mt, MessageTool) and bool(mt._messages_sent_in_turn)

    def _outbound_metadata(
        self,
        msg: "InboundMessage",
        *,
        usage: dict[str, int] | None,
        genver_handoff: Any | None,
    ) -> dict[str, Any]:
        meta = dict(msg.metadata or {})
        if usage:
            meta["usage"] = usage
        if genver_handoff is not None:
            meta["_genver_handoff"] = {
                "summary": getattr(genver_handoff, "summary", ""),
                "files_changed": list(getattr(genver_handoff, "files_changed", []) or []),
            }
        return meta

    # -- background memory extraction -----------------------------------------

    async def _background_extract(
        self,
        session: Session,
        *,
        memory: Any,
        model: str,
    ) -> None:
        """Fire-and-forget: extract durable facts from this turn's new messages.

        Uses ``memory._extract_cursor`` to track per-session progress so each
        turn only sends the delta (new messages since last extraction), not the
        entire unconsolidated tail.  This prevents repeated extraction of the
        same content across turns.

        This is the **narrative-memory lane** — it writes directly to MEMORY.md
        for human-readable project knowledge.  It is intentionally separate from
        the structured-memory lane (StructuredMemoryStore / KG) which handles
        typed task/rule/research nodes.
        """
        try:
            from src.memory.extract import extract_durable_facts, merge_extracted_facts
            from src.memory.store import MemoryStore

            total = len(session.messages)
            # Start from the higher of: last extraction cursor, or last consolidation.
            cursor = max(
                memory._extract_cursor.get(session.key, 0),
                session.last_consolidated,
            )
            new_msgs = session.messages[cursor:]
            if len(new_msgs) < 2:
                return

            facts = await extract_durable_facts(
                messages=new_msgs,
                provider=self._provider,
                model=model,
            )
            # Advance cursor regardless of whether facts were found,
            # so we don't re-scan the same messages next turn.
            memory._extract_cursor[session.key] = total

            if not facts:
                return

            workspace = memory.scope.resolve_structured_workspace(session.key)
            store = MemoryStore(workspace)
            count = merge_extracted_facts(store, facts)
            if count:
                logger.debug("Memory extraction: merged {} facts for {}", count, session.key)
                # Best-effort FTS sync so new facts are searchable immediately
                index = memory.resolve_index_for_tools(session.key)
                if index is not None:
                    try:
                        await index.sync_all(workspace / "memory")
                    except Exception:
                        logger.opt(exception=True).debug(
                            "FTS sync after extraction failed (best-effort)"
                        )
        except Exception:
            logger.opt(exception=True).debug("Background memory extraction failed")

    # -- session persistence --------------------------------------------------

    def save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        usage: dict[str, int] | None = None,
        user_message: str | None = None,
        memory_tiers: "MemoryTierManager | None" = None,
        turn_id: str | None = None,
        persisted_user_message: bool = False,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        if user_message is not None and not persisted_user_message:
            user_entry = {
                "role": "user",
                "content": user_message,
                "timestamp": datetime.now().isoformat(),
            }
            if turn_id:
                user_entry["turn_id"] = turn_id
            self._append_session_entry(session, user_entry, memory_tiers)

        for m in messages[skip:]:
            entry = {k: v for k, v in m.items() if k != "reasoning_content"}
            role, content = entry.get("role"), entry.get("content")
            if "tool_calls" in entry:
                entry["tool_calls"] = truncate_tool_call_arguments(
                    entry["tool_calls"], self._TOOL_CALL_ARGS_MAX_CHARS
                )
            if (
                role == "tool"
                and isinstance(content, str)
                and len(content) > self._TOOL_RESULT_MAX_CHARS
            ):
                entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if self._skip_persisted_user_content(content):
                    continue
                if isinstance(content, list):
                    entry["content"] = self._sanitize_multimodal_content(content)
            if turn_id and role in {"assistant", "tool", "user"}:
                entry.setdefault("turn_id", turn_id)
            entry.setdefault("timestamp", datetime.now().isoformat())

            # Attach usage to the final assistant message
            if role == "assistant" and usage and m is messages[-1]:
                entry["usage"] = usage

            self._append_session_entry(session, entry, memory_tiers)

        session.updated_at = datetime.now()

    def _append_session_entry(
        self,
        session: Session,
        entry: dict[str, Any],
        memory_tiers: "MemoryTierManager | None",
    ) -> None:
        session.messages.append(entry)
        if memory_tiers is not None:
            memory_tiers.buffer_entry(session.key, entry)

    def _skip_persisted_user_content(self, content: Any) -> bool:
        return isinstance(content, str) and (
            content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            or content.startswith(_EPHEMERAL_CONTEXT_TAG)
        )

    def _sanitize_multimodal_content(self, content: list[Any]) -> list[Any]:
        return [
            {"type": "text", "text": "[image]"} if self._is_inline_image_part(part) else part
            for part in content
        ]

    def _is_inline_image_part(self, part: Any) -> bool:
        return (
            isinstance(part, dict)
            and part.get("type") == "image_url"
            and part.get("image_url", {}).get("url", "").startswith("data:image/")
        )
