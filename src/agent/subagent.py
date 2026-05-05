"""Subagent manager for background task execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.agent.delegation.executor import SubagentExecutor
from src.agent.delegation.runtime import RuntimeRoleConfig
from src.bus.queue import MessageBus
from src.config.schema import SubagentPolicyConfig
from src.providers.base import LLMProvider
from src.session.subagent_store import SubagentStore

if TYPE_CHECKING:
    from src.config.schema import AgentRoleConfig


class SubagentManager:
    """Manages background subagent execution.

    This is a facade: all heavy lifting is delegated to
    :pyattr:`executor` (:class:`SubagentExecutor`).
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path | str,
        bus: MessageBus,
        model: str | None = None,
        roles: "dict[str, AgentRoleConfig] | None" = None,
        policy: SubagentPolicyConfig | None = None,
    ):
        self.provider = provider
        self.workspace = Path(workspace)
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.roles = roles or {}
        self.store = SubagentStore(self.workspace)
        interrupted = self.store.mark_interrupted_inflight()
        if interrupted:
            logger.warning("Marked {} in-flight subagent task(s) as interrupted", interrupted)

        self.executor = SubagentExecutor(
            policy=policy or SubagentPolicyConfig(),
            bus=bus,
            roles=self._resolve_all_roles(),
            provider=provider,
            workspace=self.workspace,
            subagent_manager=self,
            store=self.store,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        role: str | None = None,
        model_override: str | None = None,
        isolation: str | None = None,
        *,
        root_session_key: str | None = None,
        parent_task_id: str | None = None,
        depth: int | None = None,
        handoff=None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        # Validate role before delegating (preserves original error message).
        if role and role not in self.roles:
            return f"Unknown role '{role}'. Available roles: {', '.join(self.roles) or 'none configured'}"

        # Sync mutable state into executor so hot-swap works.
        self.executor._provider = self.provider
        self.executor._roles = self._resolve_all_roles()

        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        root_session = root_session_key or session_key or "cli:direct"

        result_msg = await self.executor.spawn(
            task=task,
            label=display_label,
            role=role,
            model_override=model_override,
            isolation=isolation,
            root_session_key=root_session,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            parent_task_id=parent_task_id,
            depth=depth if depth is not None else 0,
            handoff=handoff,
        )

        # If executor rejected (policy limit), forward that error.
        if result_msg.startswith("Error:"):
            return result_msg

        # Extract task_id from executor message ("Subagent started: task_id=sub-xxx, ...")
        task_id = result_msg.split("task_id=")[1].split(",")[0] if "task_id=" in result_msg else ""

        logger.info("Agent [{}]: {}", task_id, display_label)
        parts = [f"Agent [{display_label}] started (id: {task_id})."]
        if model_override:
            parts.append(f"Model: {model_override}.")
        if isolation == "worktree":
            parts.append("Running in isolated git worktree.")
        parts.append("I'll notify you when it completes.")
        return " ".join(parts)

    async def send_message(self, task_id: str, message: str) -> str:
        """Send a follow-up message to a completed agent, resuming it with context.

        If the agent is still running, returns a status message.
        If the agent has completed, spawns a new turn with the original task
        context and result as handoff, plus the new message.
        """
        record = self.executor._records.get(task_id)
        if record is None:
            return f"Error: unknown task id '{task_id}'"

        # If still running, can't send yet
        if not record.is_terminal:
            return f"Agent {task_id} is still running. Wait for it to complete before sending a message."

        # Resume: spawn a new agent with the prior context as handoff
        prior_result = record.result or record.error or "(no output)"
        context_summary = (
            f"You are continuing a previous agent task.\n\n"
            f"Original task: {record.task}\n\n"
            f"Previous result:\n{prior_result[:4000]}"
        )

        return await self.spawn(
            task=message,
            label=f"resume:{record.label}",
            origin_channel=record.origin_channel,
            origin_chat_id=record.origin_chat_id,
            role=record.role,
            model_override=record.model_override,
            root_session_key=record.root_session_key,
            parent_task_id=record.parent_task_id,
            depth=record.depth,
            handoff={"context": context_summary},
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        return await self.executor.cancel_by_session(session_key)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return self.executor.get_running_count()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_all_roles(self) -> dict[str, RuntimeRoleConfig]:
        """Convert current self.roles -> RuntimeRoleConfig dict."""
        default_model = self.model
        return {
            name: RuntimeRoleConfig.from_agent_role(name, cfg, default_model)
            for name, cfg in self.roles.items()
        }
