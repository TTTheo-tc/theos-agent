"""SubagentExecutor — core engine for delegation task trees.

Responsibilities:
  - Task tree management (records, asyncio tasks, results, consumed set)
  - spawn / wait / kill / cancel_by_session / list_tasks
  - Observability capture (elapsed, tools_used, token_usage)
  - Session-level locking (frozen_sessions pattern for cancel safety)
  - Top-level result announcement via MessageBus
  - GC for completed/consumed results
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.delegation.types import (
    HandoffSpec,
    SubagentResult,
    SubagentStatus,
    SubagentTaskRecord,
    is_terminal_status,
)

if TYPE_CHECKING:
    from src.agent.delegation.runtime import RuntimeRoleConfig
    from src.agent.tools.context import ToolContext
    from src.bus.queue import MessageBus
    from src.config.schema import SubagentPolicyConfig
    from src.providers.base import LLMProvider
    from src.session.subagent_store import SubagentStore


_DEFAULT_WAIT_TIMEOUT = 30.0
_KILL_WAIT_TIMEOUT = 2.0
_WORKTREE_ISOLATION = "worktree"


class SubagentExecutor:
    """Manages subagent lifecycle: spawn, wait, kill, cancel, GC."""

    def __init__(
        self,
        *,
        policy: SubagentPolicyConfig,
        bus: MessageBus | None,
        roles: dict[str, RuntimeRoleConfig],
        provider: LLMProvider,
        workspace: Path,
        subagent_manager: Any | None = None,
        store: "SubagentStore | None" = None,
    ) -> None:
        self._policy = policy
        self._bus = bus
        self._roles = roles
        self._provider = provider
        self._workspace = workspace

        # Task tree state
        self._records: dict[str, SubagentTaskRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, SubagentResult] = {}
        self._consumed: set[str] = set()

        # Session-level lock and frozen set for cancel safety
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._frozen_sessions: set[str] = set()
        self._subagent_manager = subagent_manager
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        *,
        task: str,
        label: str | None = None,
        role: str | None,
        model_override: str | None = None,
        isolation: str | None = None,
        root_session_key: str,
        origin_channel: str,
        origin_chat_id: str,
        parent_task_id: str | None = None,
        depth: int = 0,
        handoff: HandoffSpec | dict[str, Any] | None = None,
    ) -> str:
        """Spawn a new subagent task. Returns a status message."""
        display_label = label or role or task[:30] or "subagent"
        handoff_spec = self._coerce_handoff(handoff)

        lock = self._get_session_lock(root_session_key)
        async with lock:
            if error := self._spawn_policy_error(
                root_session_key=root_session_key,
                parent_task_id=parent_task_id,
                depth=depth,
                isolation=isolation,
            ):
                return error

            task_id = f"sub-{uuid.uuid4().hex[:12]}"
            record = SubagentTaskRecord(
                task_id=task_id,
                task=task,
                label=display_label,
                role=role,
                parent_task_id=parent_task_id,
                root_session_key=root_session_key,
                depth=depth,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                handoff=handoff_spec,
                model_override=model_override,
                isolation=isolation,
            )
            self._records[task_id] = record
            self._record_checkpoint(
                record,
                "pending",
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
            )

            # Create asyncio task
            coro = self._execute(task_id)
            asyncio_task = asyncio.create_task(coro, name=f"subagent-{task_id}")
            self._tasks[task_id] = asyncio_task

            logger.info(
                "[Executor] Spawned task_id={} label={!r} role={!r} depth={}",
                task_id,
                display_label,
                role,
                depth,
            )
            return f"Subagent started: task_id={task_id}, label={display_label!r}"

    async def wait(
        self,
        task_id: str,
        *,
        timeout: float | None = None,
        timeout_seconds: float | None = None,
        context: ToolContext | None = None,
    ) -> SubagentResult | None:
        """Wait for a task to complete. Returns result or status snapshot."""
        if not self._can_access_task(task_id, context):
            return None

        resolved_timeout = timeout if timeout is not None else timeout_seconds
        if resolved_timeout is None:
            resolved_timeout = _DEFAULT_WAIT_TIMEOUT

        # Check results cache first
        if task_id in self._results:
            result = self._results[task_id]
            if is_terminal_status(result.status):
                self._consumed.add(task_id)
            return result

        record = self._records.get(task_id)
        if record is None:
            return None

        # If already terminal, build result
        if record.is_terminal:
            self._consumed.add(task_id)
            return self._build_result(record)

        # Wait for the asyncio task
        asyncio_task = self._tasks.get(task_id)
        if asyncio_task is None:
            if record.is_terminal:
                self._consumed.add(task_id)
            return self._build_result(record)

        try:
            await asyncio.wait_for(asyncio.shield(asyncio_task), timeout=resolved_timeout)
        except asyncio.TimeoutError:
            # Return current status snapshot
            pass
        except asyncio.CancelledError:
            pass

        result = self._build_result(record)
        if is_terminal_status(result.status):
            self._consumed.add(task_id)
        return result

    async def kill(
        self,
        task_id: str,
        *,
        cascade: bool = True,
        context: ToolContext | None = None,
    ) -> bool:
        """Cancel a task and cascade to children. Returns True if found."""
        if not self._can_access_task(task_id, context):
            return False

        asyncio_task = self._tasks.get(task_id)
        if asyncio_task is None:
            return False

        record = self._records.get(task_id)
        if record is not None:
            record.cancel_children_on_finish = cascade

        if cascade:
            await self._cancel_running_children(task_id)

        asyncio_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(asyncio_task), timeout=_KILL_WAIT_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return True

    async def cancel_by_session(self, root_session_key: str) -> int:
        """Cancel all tasks for a session. Returns count cancelled."""
        lock = self._get_session_lock(root_session_key)
        async with lock:
            self._frozen_sessions.add(root_session_key)
            try:
                task_ids = [
                    tid
                    for tid, rec in self._records.items()
                    if rec.root_session_key == root_session_key and not rec.is_terminal
                ]
                await self._cancel_task_ids(task_ids)
                return len(task_ids)
            finally:
                self._frozen_sessions.discard(root_session_key)

    def list_tasks(
        self,
        root_session_key: str | None = None,
        *,
        context: ToolContext | None = None,
    ) -> list[SubagentTaskRecord]:
        """List all tasks for a session."""
        records = list(self._records.values())
        if root_session_key is not None:
            records = [rec for rec in records if rec.root_session_key == root_session_key]
        if context is None:
            return records

        session_scope = context.root_session_key or context.session_key
        if session_scope:
            records = [rec for rec in records if rec.root_session_key == session_scope]
        if context.subagent_task_id:
            root_task_id = context.subagent_task_id
            records = [
                rec
                for rec in records
                if rec.task_id == root_task_id or self._is_descendant(rec.task_id, root_task_id)
            ]
        return records

    def get_running_count(self) -> int:
        """Count currently running (non-terminal) tasks."""
        return sum(1 for r in self._records.values() if not r.is_terminal)

    # ------------------------------------------------------------------
    # Internal: execution
    # ------------------------------------------------------------------

    async def _execute(self, task_id: str) -> None:
        """Run a subagent loop, capture observability, handle lifecycle."""
        record = self._records[task_id]
        record.status = SubagentStatus.RUNNING
        record.started_at = time.time()
        self._record_checkpoint(record, SubagentStatus.RUNNING.value)

        # Determine timeout
        role_config = self._roles.get(record.role) if record.role else None
        timeout_s = (
            role_config.timeout_seconds if role_config and role_config.timeout_seconds else None
        ) or self._policy.timeout_seconds

        if record.isolation == _WORKTREE_ISOLATION:
            await self._setup_worktree(record)

        t0 = time.monotonic()
        try:
            content, tools_used, _messages, usage = await asyncio.wait_for(
                self._run_subagent_loop(record, role_config),
                timeout=timeout_s,
            )
            elapsed = time.monotonic() - t0

            self._finish_record(
                record,
                SubagentStatus.COMPLETED,
                elapsed_seconds=round(elapsed, 2),
                result=content,
                tools_used=tools_used,
                token_usage=usage,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            self._finish_record(
                record,
                SubagentStatus.TIMED_OUT,
                elapsed_seconds=round(elapsed, 2),
                error=f"Timed out after {timeout_s}s",
            )

        except asyncio.CancelledError:
            elapsed = time.monotonic() - t0
            self._finish_record(
                record,
                SubagentStatus.CANCELLED,
                elapsed_seconds=round(elapsed, 2),
            )

        except Exception as exc:
            elapsed = time.monotonic() - t0
            self._finish_record(
                record,
                SubagentStatus.FAILED,
                elapsed_seconds=round(elapsed, 2),
                error=str(exc),
            )
            logger.opt(exception=True).warning("[Executor] Task {} failed: {}", task_id, exc)

        finally:
            # Cascade-cancel children
            if record.cancel_children_on_finish:
                await self._cancel_running_children(task_id)

            # Clean up worktree if no changes were made
            if record.worktree_path:
                await self._cleanup_worktree(record)

            # Remove asyncio task reference
            self._tasks.pop(task_id, None)

            # Announce top-level results
            if record.parent_task_id is None:
                await self._announce_top_level_result(record)
                self._consumed.add(task_id)

            # GC
            self._gc_results()

            logger.info(
                "[Executor] Task {} finished status={} elapsed={:.1f}s",
                task_id,
                record.status,
                (record.finished_at or time.time()) - (record.started_at or record.created_at),
            )

    async def _run_subagent_loop(
        self,
        record: SubagentTaskRecord,
        role_config: RuntimeRoleConfig | None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
        """Build tools, context, prompt, and run the tool loop."""
        from src.agent.loop_core import run_tool_loop
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.context import ToolContext
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        # Use worktree workspace if available, otherwise default
        workspace = record.worktree_path or self._workspace

        registry = ToolRegistry()

        reg_config = ToolRegistrationConfig(
            workspace=workspace,
            mode="subagent",
            allowed_tools=role_config.allowed_tools if role_config else None,
            executor=self,
            subagent_manager=self._subagent_manager,
            provider=self._provider,
        )
        register_standard_tools(registry, reg_config)

        tool_context = ToolContext(
            channel=record.origin_channel,
            chat_id=record.origin_chat_id,
            session_key=f"subagent:{record.task_id}",
            sender_id="subagent",
            sender_is_owner=False,
            root_session_key=record.root_session_key,
            subagent_task_id=record.task_id,
            spawn_depth=record.depth,
            allow_subagent_spawn=(role_config.allow_nested_spawn if role_config else False),
        )

        prompt = self._build_prompt(
            task=record.task,
            role_config=role_config,
            handoff=record.handoff,
        )

        messages: list[dict] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": record.task},
        ]

        # Model priority: explicit override > role config > provider default
        model = (
            record.model_override
            or (role_config.model if role_config else None)
            or self._provider.get_default_model()
        )
        max_iter = role_config.max_iterations if role_config else 30

        return await run_tool_loop(
            provider=self._provider,
            messages=messages,
            tools=registry,
            model=model,
            temperature=0.1,
            max_tokens=4096,
            max_iterations=max_iter,
            tool_context=tool_context,
        )

    # ------------------------------------------------------------------
    # Worktree isolation
    # ------------------------------------------------------------------

    async def _setup_worktree(self, record: SubagentTaskRecord) -> None:
        """Create a git worktree for isolated subagent execution."""
        import subprocess

        # Check if workspace is a git repo
        git_dir = self._workspace / ".git"
        if not git_dir.exists():
            logger.warning(
                "[Executor] Worktree requested for {} but workspace is not a git repo; "
                "falling back to shared workspace.",
                record.task_id,
            )
            record.isolation = None
            return

        branch_name = f"agent/{record.task_id}"
        worktree_base = self._workspace / ".agent-worktrees"
        worktree_path = worktree_base / record.task_id

        try:
            worktree_base.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
                cwd=str(self._workspace),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning(
                    "[Executor] Failed to create worktree for {}: {}",
                    record.task_id,
                    proc.stderr.strip(),
                )
                record.isolation = None
                return

            record.worktree_path = worktree_path
            record.worktree_branch = branch_name
            logger.info(
                "[Executor] Created worktree for {} at {}",
                record.task_id,
                worktree_path,
            )
        except Exception as exc:
            logger.warning(
                "[Executor] Worktree setup failed for {}: {}",
                record.task_id,
                exc,
            )
            record.isolation = None

    async def _cleanup_worktree(self, record: SubagentTaskRecord) -> None:
        """Remove worktree if no changes were made; keep it otherwise."""
        import subprocess

        if not record.worktree_path or not record.worktree_path.exists():
            return

        try:
            # Check if the worktree has any changes
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "status", "--porcelain"],
                cwd=str(record.worktree_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            has_changes = bool(proc.stdout.strip())

            if not has_changes:
                # No changes — clean up worktree and branch
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "worktree", "remove", str(record.worktree_path)],
                    cwd=str(self._workspace),
                    capture_output=True,
                    timeout=10,
                )
                if record.worktree_branch:
                    await asyncio.to_thread(
                        subprocess.run,
                        ["git", "branch", "-D", record.worktree_branch],
                        cwd=str(self._workspace),
                        capture_output=True,
                        timeout=10,
                    )
                record.worktree_path = None
                record.worktree_branch = None
                logger.info("[Executor] Cleaned up worktree for {} (no changes)", record.task_id)
            else:
                logger.info(
                    "[Executor] Worktree preserved for {} at {} (branch: {})",
                    record.task_id,
                    record.worktree_path,
                    record.worktree_branch,
                )
        except Exception as exc:
            logger.warning(
                "[Executor] Worktree cleanup failed for {}: {}",
                record.task_id,
                exc,
            )

    def _build_prompt(
        self,
        *,
        task: str,
        role_config: RuntimeRoleConfig | None,
        handoff: HandoffSpec | None = None,
    ) -> str:
        """Build system prompt from role config and handoff spec."""
        parts: list[str] = []

        if role_config and role_config.system_prompt:
            parts.append(role_config.system_prompt)
        else:
            parts.append("You are a focused subagent. Complete the given task concisely.")

        if handoff:
            parts.append("\n## Handoff Context")
            if handoff.context:
                parts.append(handoff.context)
            if handoff.acceptance_criteria:
                parts.append(f"\n**Acceptance Criteria:** {handoff.acceptance_criteria}")
            if handoff.not_in_scope:
                parts.append(f"\n**Not In Scope:** {handoff.not_in_scope}")
            if handoff.constraints:
                parts.append(f"\n**Constraints:** {handoff.constraints}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Announcement
    # ------------------------------------------------------------------

    async def _announce_top_level_result(self, record: SubagentTaskRecord) -> None:
        """Publish a root-session message asking the main agent to summarize the result."""
        if self._bus is None:
            return

        from src.bus.events import InboundMessage

        status_text = "completed successfully" if record.result and not record.error else "failed"
        result_body = record.result if record.result else (record.error or "No output.")
        content = f"""[Subagent '{record.label}' {status_text}]

Task: {record.task}

Result:
{result_body}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{record.origin_channel}:{record.origin_chat_id}",
            content=content,
            session_key_override=record.root_session_key,
        )
        await self._bus.publish_inbound(msg)
        logger.debug(
            "Subagent [{}] announced result to {}:{}",
            record.task_id,
            record.origin_channel,
            record.origin_chat_id,
        )

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------

    def _gc_results(self) -> None:
        """Evict consumed/orphan results beyond keep_completed."""
        terminal = [(tid, rec) for tid, rec in self._records.items() if rec.is_terminal]
        if len(terminal) <= self._policy.keep_completed:
            return

        # Sort by finished_at (oldest first)
        terminal.sort(key=lambda x: x[1].finished_at or 0)

        # Evict consumed or orphan (no parent) results from oldest
        to_evict = len(terminal) - self._policy.keep_completed
        evicted = 0
        for tid, rec in terminal:
            if evicted >= to_evict:
                break
            if tid in self._consumed:
                self._records.pop(tid, None)
                self._results.pop(tid, None)
                self._consumed.discard(tid)
                evicted += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        if session_key not in self._session_locks:
            self._session_locks[session_key] = asyncio.Lock()
        return self._session_locks[session_key]

    def _spawn_policy_error(
        self,
        *,
        root_session_key: str,
        parent_task_id: str | None,
        depth: int,
        isolation: str | None,
    ) -> str | None:
        if root_session_key in self._frozen_sessions:
            return "Error: session is being cancelled, cannot spawn new tasks."

        if depth >= self._policy.max_depth:
            return (
                f"Error: max depth ({self._policy.max_depth}) reached. "
                f"Cannot spawn at depth {depth}."
            )

        running = self.get_running_count()
        if running >= self._policy.max_concurrent:
            return (
                f"Error: concurrent limit ({self._policy.max_concurrent}) reached. "
                f"{running} tasks running."
            )

        if parent_task_id is not None:
            children_count = len(self._non_terminal_children(parent_task_id))
            if children_count >= self._policy.max_children_per_agent:
                return (
                    f"Error: children limit ({self._policy.max_children_per_agent}) "
                    f"reached for parent {parent_task_id}."
                )

        if isolation and isolation != _WORKTREE_ISOLATION:
            return (
                f"Error: unknown isolation mode '{isolation}'. "
                f"Only '{_WORKTREE_ISOLATION}' is supported."
            )

        return None

    def _finish_record(
        self,
        record: SubagentTaskRecord,
        status: SubagentStatus,
        *,
        elapsed_seconds: float,
        result: str | None = None,
        error: str | None = None,
        tools_used: list[str] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        record.status = status
        record.result = result
        record.error = error
        record.finished_at = time.time()

        self._results[record.task_id] = SubagentResult(
            task_id=record.task_id,
            status=status,
            role=record.role,
            parent_task_id=record.parent_task_id,
            depth=record.depth,
            result=result,
            error=error,
            elapsed_seconds=elapsed_seconds,
            tools_used=tools_used,
            token_usage=token_usage,
        )

        metadata: dict[str, Any] = {"elapsed_seconds": elapsed_seconds}
        if tools_used is not None:
            metadata["tools_used"] = tools_used
        if token_usage is not None:
            metadata["token_usage"] = token_usage
        if error is not None:
            metadata["error"] = error
        if isinstance(result, str):
            metadata["result_preview"] = result[:200]

        self._record_checkpoint(record, status.value, **metadata)

    def _record_checkpoint(
        self,
        record: SubagentTaskRecord,
        status: str,
        **metadata: Any,
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.record(
                record.root_session_key,
                record.task_id,
                status,
                label=record.label,
                role=record.role,
                parent_task_id=record.parent_task_id,
                depth=record.depth,
                task=record.task,
                **metadata,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "[Executor] Failed to record checkpoint {} for {}",
                status,
                record.task_id,
            )

    def _coerce_handoff(self, handoff: HandoffSpec | dict[str, Any] | None) -> HandoffSpec | None:
        if handoff is None or isinstance(handoff, HandoffSpec):
            return handoff
        return HandoffSpec(
            context=str(handoff.get("context", "")),
            constraints=handoff.get("constraints"),
            acceptance_criteria=handoff.get("acceptance_criteria"),
            not_in_scope=handoff.get("not_in_scope"),
        )

    async def _cancel_running_children(self, task_id: str) -> None:
        children = self._non_terminal_children(task_id)
        await self._cancel_task_ids(children)

    async def _cancel_task_ids(self, task_ids: list[str]) -> None:
        tasks: list[asyncio.Task] = []
        for task_id in task_ids:
            task = self._tasks.get(task_id)
            if task and not task.done():
                task.cancel()
                tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _non_terminal_children(self, task_id: str) -> list[str]:
        return [
            tid
            for tid, record in self._records.items()
            if record.parent_task_id == task_id and not record.is_terminal
        ]

    def _is_descendant(self, task_id: str, ancestor_task_id: str) -> bool:
        current = self._records.get(task_id)
        while current is not None and current.parent_task_id is not None:
            if current.parent_task_id == ancestor_task_id:
                return True
            current = self._records.get(current.parent_task_id)
        return False

    def _can_access_task(self, task_id: str, context: ToolContext | None) -> bool:
        record = self._records.get(task_id)
        if record is None:
            return False
        if context is None:
            return True

        session_scope = context.root_session_key or context.session_key
        if session_scope and record.root_session_key != session_scope:
            return False
        if context.subagent_task_id:
            return task_id == context.subagent_task_id or self._is_descendant(
                task_id, context.subagent_task_id
            )
        return True

    def _build_result(self, record: SubagentTaskRecord) -> SubagentResult:
        """Build a SubagentResult snapshot from current record state."""
        if record.task_id in self._results:
            return self._results[record.task_id]
        return SubagentResult(
            task_id=record.task_id,
            status=record.status,
            role=record.role,
            parent_task_id=record.parent_task_id,
            depth=record.depth,
            result=record.result,
            error=record.error,
        )
