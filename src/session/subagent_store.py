"""Durable subagent/background task storage for session inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.session.checkpoint_utils import (
    append_checkpoint_row,
    checkpoint_metadata,
    checkpoint_path,
    checkpoint_timestamp,
    iter_checkpoint_rows,
    jsonable_metadata,
)
from src.utils.helpers import ensure_dir

_TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled", "interrupted"})


@dataclass
class SubagentCheckpoint:
    """A durable state transition for one subagent task."""

    task_id: str
    session_key: str
    status: str
    timestamp: str
    metadata: dict[str, Any]

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "_type": "subagent_checkpoint",
            "task_id": self.task_id,
            "session_key": self.session_key,
            "status": self.status,
            "timestamp": self.timestamp,
            **self.metadata,
        }


class SubagentStore:
    """Append-only per-session checkpoint store for subagent lifecycle."""

    def __init__(self, workspace: Path) -> None:
        self.base_dir = ensure_dir(workspace / "subagents")

    def _get_path(self, session_key: str) -> Path:
        return checkpoint_path(self.base_dir, session_key)

    def record(
        self, session_key: str, task_id: str, status: str, **metadata: Any
    ) -> SubagentCheckpoint:
        checkpoint = SubagentCheckpoint(
            task_id=task_id,
            session_key=session_key,
            status=status,
            timestamp=checkpoint_timestamp(),
            metadata=jsonable_metadata(metadata),
        )
        append_checkpoint_row(self._get_path(session_key), checkpoint.to_dict())
        return checkpoint

    def latest_for_session(self, session_key: str, limit: int = 5) -> list[SubagentCheckpoint]:
        latest = list(self._latest_by_task(session_key).values())
        latest.sort(key=lambda cp: cp.timestamp, reverse=True)
        return latest[:limit]

    def active_for_session(self, session_key: str) -> list[SubagentCheckpoint]:
        latest = list(self._latest_by_task(session_key).values())
        active = [cp for cp in latest if not cp.is_terminal]
        active.sort(key=lambda cp: cp.timestamp, reverse=True)
        return active

    def mark_interrupted_inflight(self, reason: str = "gateway restart") -> int:
        marked = 0
        for path in self.base_dir.glob("*.jsonl"):
            latest_by_task = self._latest_by_task_from_path(path)
            for cp in latest_by_task.values():
                if cp.is_terminal:
                    continue
                self.record(
                    cp.session_key,
                    cp.task_id,
                    "interrupted",
                    reason=reason,
                    interrupted_from=cp.status,
                    label=cp.metadata.get("label"),
                    role=cp.metadata.get("role"),
                    task=cp.metadata.get("task"),
                    depth=cp.metadata.get("depth"),
                )
                marked += 1
        return marked

    def _latest_by_task(self, session_key: str) -> dict[str, SubagentCheckpoint]:
        path = self._get_path(session_key)
        return self._latest_by_task_from_path(path)

    def _latest_by_task_from_path(self, path: Path) -> dict[str, SubagentCheckpoint]:
        latest: dict[str, SubagentCheckpoint] = {}
        try:
            for row in iter_checkpoint_rows(path, "subagent_checkpoint"):
                cp = self._from_row(row)
                if cp is not None:
                    latest[cp.task_id] = cp
        except Exception:
            logger.opt(exception=True).warning("Failed to read subagent checkpoints from {}", path)
        return latest

    @staticmethod
    def _from_row(row: dict[str, Any] | None) -> SubagentCheckpoint | None:
        if not row:
            return None
        return SubagentCheckpoint(
            task_id=row.get("task_id", ""),
            session_key=row.get("session_key", ""),
            status=row.get("status", ""),
            timestamp=row.get("timestamp", ""),
            metadata=checkpoint_metadata(row, "task_id"),
        )
