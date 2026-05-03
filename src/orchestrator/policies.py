"""Pluggable execution policies for TurnLifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.orchestrator.state_machine import TaskRecord, TaskState

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop
    from src.bus.events import InboundMessage
    from src.config.schema import EventStoreConfig
    from src.orchestrator.turn_record import TurnRecord
    from src.store.database import Database
    from src.store.event_store import EventStore


class ExecutionPolicy:
    """Hook-point interface for turn execution policies.

    All methods have default no-op implementations. Override only what you need.

    Constraints:
    - should_retry() is decision-only — no state mutation, no side effects
    - on_retry() fires AFTER should_retry returns True, BEFORE the next attempt
    - after_failure() fires ONLY on terminal failure (all retries exhausted)
    """

    async def before_execute(self, turn: TurnRecord, msg: InboundMessage) -> None:
        """Called before _process_message."""

    async def after_success(self, turn: TurnRecord, msg: InboundMessage, response: Any) -> None:
        """Called after successful _process_message."""

    async def after_failure(self, turn: TurnRecord, msg: InboundMessage, error: Exception) -> None:
        """Called on terminal failure (all retries exhausted or no retry policy)."""

    async def on_retry(
        self, turn: TurnRecord, msg: InboundMessage, error: Exception, attempt: int
    ) -> None:
        """Called when should_retry returned True, BEFORE the next attempt."""

    def should_retry(self, turn: TurnRecord, error: Exception) -> bool:
        """Pure retry decision. Must not mutate state or fire side effects."""
        return False

    async def close(self) -> None:
        """Cleanup resources owned by this policy."""


class OrchestratorPolicy(ExecutionPolicy):
    """Wraps the existing Orchestrator retry/review/event-store logic as a Policy.

    Concurrency note: ``PerGroupDispatcher`` serializes within a session, but
    different sessions run concurrently.  All per-turn state is keyed by
    ``turn_id`` in ``_active`` dict, never stored as a single ``self._task``
    instance variable.
    """

    def __init__(
        self,
        *,
        max_retries: int = 3,
        review_mode: str = "auto",
        event_log_enabled: bool = True,
        event_store_config: EventStoreConfig | None = None,
        agent: AgentLoop,
    ) -> None:
        self.max_retries = max_retries
        self.review_mode = review_mode
        self.event_log_enabled = event_log_enabled
        self._event_store_config = event_store_config
        self._agent = agent
        self._tasks: dict[str, TaskRecord] = {}  # task_id -> TaskRecord (all tasks)
        self._active: dict[str, TaskRecord] = {}  # turn_id -> TaskRecord (in-flight only)
        self._db: Database | None = None
        self._event_store: EventStore | None = None

    # ------------------------------------------------------------------
    # Internal helpers (ported from Orchestrator)
    # ------------------------------------------------------------------

    def _get_task(self, turn: TurnRecord) -> TaskRecord | None:
        """Look up the in-flight TaskRecord for a given turn."""
        return self._active.get(turn.turn_id)

    async def _ensure_db(self) -> None:
        """Lazy-init SQLite database and EventStore when event_store is enabled."""
        if (
            self._db is not None
            or not self._event_store_config
            or not self._event_store_config.enabled
        ):
            return
        from src.store.database import Database
        from src.store.event_store import EventStore

        db_path = Path(self._agent.workspace) / self._event_store_config.db_name
        self._db = Database(db_path)
        await self._db.connect()
        self._event_store = EventStore(self._db)
        logger.info("EventStore connected: {}", db_path)

    def _build_event_callback(self, session_key: str) -> Any:
        """Build an async callback for persisting events to the EventStore.

        Returns ``None`` when the EventStore is not enabled.
        """
        if not self._event_store:
            return None
        es = self._event_store

        async def _persist(event: dict, *, _sk: str = session_key) -> None:
            try:
                await es.append(event.get("task_id", ""), _sk, event)
            except Exception:
                logger.opt(exception=True).warning("EventStore append failed")

        return _persist

    def _should_review(self, task: TaskRecord) -> bool:
        """Decide whether this task needs a REVIEWING phase."""
        if self.review_mode == "never":
            return False
        if self.review_mode == "always":
            return True
        # auto: review only when GenVer mode is active and handoff is present
        return self._agent._is_genver and task.handoff is not None

    # ------------------------------------------------------------------
    # Public accessors (preserve Orchestrator API surface)
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Look up a TaskRecord by its task_id."""
        return self._tasks.get(task_id)

    @property
    def active_tasks(self) -> list[TaskRecord]:
        """Return all non-terminal tasks."""
        return [t for t in self._tasks.values() if not t.is_terminal]

    # ------------------------------------------------------------------
    # ExecutionPolicy hooks
    # ------------------------------------------------------------------

    async def before_execute(self, turn: TurnRecord, msg: InboundMessage) -> None:
        """Create a TaskRecord, store in registries, transition to EXECUTING."""
        await self._ensure_db()
        on_event = self._build_event_callback(msg.session_key)
        task = TaskRecord(
            session_key=turn.session_key,
            turn_id=turn.turn_id,
            max_retries=self.max_retries,
            event_log_enabled=self.event_log_enabled,
            _on_event=on_event,
        )
        self._tasks[task.task_id] = task
        self._active[turn.turn_id] = task
        task.transition(TaskState.EXECUTING)
        logger.debug(
            "OrchestratorPolicy task {} created for session {}",
            task.task_id,
            msg.session_key,
        )

    def should_retry(self, turn: TurnRecord, error: Exception) -> bool:
        """Decision-only: can this turn retry? No side effects."""
        task = self._get_task(turn)
        return task is not None and task.can_retry

    async def on_retry(
        self, turn: TurnRecord, msg: InboundMessage, error: Exception, attempt: int
    ) -> None:
        """Retry side effects: state transitions + per-attempt post-chat hook."""
        task = self._get_task(turn)
        if not task:
            return
        task.error = str(error)
        task.transition(TaskState.EXEC_FAILED, error=str(error))
        task.retry_count += 1
        task.transition(TaskState.EXECUTING)  # re-enter for next attempt
        logger.info(
            "OrchestratorPolicy task {} retrying ({}/{})",
            task.task_id,
            task.retry_count,
            task.max_retries,
        )
        asyncio.create_task(
            self._agent.hooks.run_post_chat(
                msg.session_key,
                error=str(error),
                status="failed",
                user_message=msg.content,
                tools_used=[],
                usage={},
                duration_ms=None,
                routing_domains=[],
                selected_primary=None,
                artifacts=[],
                tests=[],
                workspace=self._agent.workspace,
            )
        )

    async def after_success(self, turn: TurnRecord, msg: InboundMessage, response: Any) -> None:
        """Handle successful execution: genver handoff, review gate, approval."""
        task = self._get_task(turn)
        if not task:
            return
        task.result = response.content if response else None

        # Retrieve genver handoff for this session
        handoff = self._agent.pop_genver_handoff(turn.session_key)
        if handoff is not None:
            task.handoff = {
                "summary": handoff.summary,
                "files_changed": handoff.files_changed,
            }

        # Success path: decide whether to review
        if self._should_review(task):
            task.transition(TaskState.REVIEWING)
        task.transition(TaskState.APPROVED)

        logger.debug(
            "OrchestratorPolicy task {} approved (retries={})",
            task.task_id,
            task.retry_count,
        )
        self._active.pop(turn.turn_id, None)

    async def after_failure(self, turn: TurnRecord, msg: InboundMessage, error: Exception) -> None:
        """Terminal failure only — all retries exhausted."""
        task = self._get_task(turn)
        if not task:
            return
        task.error = str(error)
        if task.state != TaskState.FAILED:
            # Transition through EXEC_FAILED if needed
            if task.state == TaskState.EXECUTING:
                task.transition(TaskState.EXEC_FAILED, error=str(error))
            task.transition(TaskState.FAILED)
        logger.warning(
            "OrchestratorPolicy task {} permanently failed after {} retries",
            task.task_id,
            task.retry_count,
        )
        self._active.pop(turn.turn_id, None)

    async def close(self) -> None:
        """Close policy-owned EventStore resources when lifecycle shuts down."""
        if self._db:
            await self._db.close()
            self._db = None
            self._event_store = None
