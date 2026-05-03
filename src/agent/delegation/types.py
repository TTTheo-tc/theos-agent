"""Data types for delegation task trees."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class SubagentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


_TERMINAL = frozenset(
    {
        SubagentStatus.COMPLETED,
        SubagentStatus.FAILED,
        SubagentStatus.TIMED_OUT,
        SubagentStatus.CANCELLED,
    }
)


@dataclass
class HandoffSpec:
    """Structured handoff protocol (MX-2). Phase 1: data carrier only."""

    context: str = ""
    constraints: dict | None = None
    acceptance_criteria: str | None = None
    not_in_scope: str | None = None


@dataclass
class SubagentTaskRecord:
    task_id: str
    task: str
    label: str
    role: str | None
    parent_task_id: str | None
    root_session_key: str
    depth: int
    origin_channel: str
    origin_chat_id: str
    status: SubagentStatus = SubagentStatus.PENDING
    result: str | None = None
    error: str | None = None
    handoff: HandoffSpec | None = None
    model_override: str | None = None
    isolation: str | None = None
    worktree_path: Path | None = None
    worktree_branch: str | None = None
    cancel_children_on_finish: bool = True
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


@dataclass
class SubagentResult:
    task_id: str
    status: SubagentStatus
    role: str | None
    parent_task_id: str | None
    depth: int
    result: str | None = None
    error: str | None = None
    elapsed_seconds: float | None = None
    tools_used: list[str] | None = None
    token_usage: dict[str, int] | None = None
