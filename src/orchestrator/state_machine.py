"""TaskRecord and TaskState — deterministic task lifecycle state machine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class TaskState(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXEC_FAILED = "exec_failed"
    FAILED = "failed"


# Valid state transitions: current_state -> set of allowed next states
_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.EXECUTING},
    TaskState.EXECUTING: {TaskState.REVIEWING, TaskState.APPROVED, TaskState.EXEC_FAILED},
    TaskState.REVIEWING: {TaskState.APPROVED, TaskState.REJECTED},
    TaskState.REJECTED: {TaskState.EXECUTING},
    TaskState.EXEC_FAILED: {TaskState.EXECUTING, TaskState.FAILED},
    TaskState.APPROVED: set(),
    TaskState.FAILED: set(),
}


@dataclass
class TaskRecord:
    """Tracks the lifecycle of a single inbound message through the agent pipeline."""

    session_key: str
    turn_id: str | None = None
    state: TaskState = TaskState.PENDING
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    result: str | None = None
    error: str | None = None
    handoff: dict[str, Any] | None = None
    event_log_enabled: bool = True
    event_log: list[dict[str, Any]] = field(default_factory=list)
    _on_event: Callable | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._log_event("created", state=self.state.value)

    def transition(self, new_state: TaskState, **metadata: Any) -> None:
        """Transition to a new state with validation and event logging.

        Raises ValueError if the transition is not allowed.
        """
        allowed = _TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self.state.value} -> {new_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        old_state = self.state
        self.state = new_state
        self.updated_at = datetime.now()
        self._log_event(
            "transition",
            old_state=old_state.value,
            new_state=new_state.value,
            **metadata,
        )

    @property
    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    @property
    def is_terminal(self) -> bool:
        return self.state in (TaskState.APPROVED, TaskState.FAILED)

    def _log_event(self, event_type: str, **data: Any) -> None:
        if not self.event_log_enabled:
            return
        import asyncio

        event = {"type": event_type, "timestamp": datetime.now().isoformat(), **data}
        self.event_log.append(event)
        if self._on_event is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._on_event(event))
            except RuntimeError:
                pass  # No running loop (e.g. during __post_init__ in sync code)
