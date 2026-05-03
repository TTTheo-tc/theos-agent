"""Durable turn checkpoint storage for resume and recovery semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.helpers import ensure_dir, safe_filename

_TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


@dataclass
class TurnCheckpoint:
    """A durable checkpoint in a single turn's lifecycle."""

    turn_id: str
    session_key: str
    status: str
    timestamp: str
    metadata: dict[str, Any]

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "_type": "turn_checkpoint",
            "turn_id": self.turn_id,
            "session_key": self.session_key,
            "status": self.status,
            "timestamp": self.timestamp,
            **self.metadata,
        }


class TurnStore:
    """Append-only JSONL store for turn checkpoints.

    One file per session key. This keeps turn/runtime state recoverable without
    polluting the main transcript in ``sessions/*.jsonl``.
    """

    def __init__(self, workspace: Path) -> None:
        self.turns_dir = ensure_dir(workspace / "turns")

    def _get_path(self, session_key: str) -> Path:
        safe_key = safe_filename(session_key.replace(":", "_"))
        return self.turns_dir / f"{safe_key}.jsonl"

    def record(
        self, session_key: str, turn_id: str, status: str, **metadata: Any
    ) -> TurnCheckpoint:
        """Append one checkpoint."""
        checkpoint = TurnCheckpoint(
            turn_id=turn_id,
            session_key=session_key,
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=self._jsonable(metadata),
        )
        path = self._get_path(session_key)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(checkpoint.to_dict(), ensure_ascii=False) + "\n")
        return checkpoint

    def latest(self, session_key: str) -> TurnCheckpoint | None:
        """Return the latest checkpoint for a session, if any."""
        path = self._get_path(session_key)
        if not path.exists():
            return None
        try:
            latest_row: dict[str, Any] | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("_type") == "turn_checkpoint":
                        latest_row = row
            return self._from_row(latest_row) if latest_row else None
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to read latest turn checkpoint for {}", session_key
            )
            return None

    def list_latest(self, limit: int = 20) -> list[TurnCheckpoint]:
        """Return latest checkpoint per session, newest first."""
        checkpoints: list[TurnCheckpoint] = []
        for path in self.turns_dir.glob("*.jsonl"):
            cp = self._latest_from_path(path)
            if cp is not None:
                checkpoints.append(cp)
        checkpoints.sort(key=lambda cp: cp.timestamp, reverse=True)
        return checkpoints[:limit]

    def mark_interrupted_inflight(self, reason: str = "gateway restart") -> int:
        """Mark all non-terminal latest checkpoints as interrupted.

        Call once during process startup to convert abandoned in-flight turns
        from the previous process into durable interrupted state.
        """
        marked = 0
        for path in self.turns_dir.glob("*.jsonl"):
            latest = self._latest_from_path(path)
            if latest is None or latest.is_terminal:
                continue
            meta = {"reason": reason, "interrupted_from": latest.status}
            if "question" in latest.metadata:
                meta["question"] = latest.metadata["question"]
            self.record(latest.session_key, latest.turn_id, "interrupted", **meta)
            marked += 1
        return marked

    @staticmethod
    def _jsonable(metadata: dict[str, Any]) -> dict[str, Any]:
        def _convert(value: Any) -> Any:
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {str(k): _convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [_convert(v) for v in value]
            return str(value)

        return {str(k): _convert(v) for k, v in metadata.items() if v is not None}

    def _latest_from_path(self, path: Path) -> TurnCheckpoint | None:
        try:
            latest_row: dict[str, Any] | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("_type") == "turn_checkpoint":
                        latest_row = row
            return self._from_row(latest_row) if latest_row else None
        except Exception:
            logger.opt(exception=True).warning("Failed to read turn checkpoints from {}", path)
            return None

    @staticmethod
    def _from_row(row: dict[str, Any] | None) -> TurnCheckpoint | None:
        if not row:
            return None
        metadata = {
            k: v
            for k, v in row.items()
            if k not in {"_type", "turn_id", "session_key", "status", "timestamp"}
        }
        return TurnCheckpoint(
            turn_id=row.get("turn_id", ""),
            session_key=row.get("session_key", ""),
            status=row.get("status", ""),
            timestamp=row.get("timestamp", ""),
            metadata=metadata,
        )
