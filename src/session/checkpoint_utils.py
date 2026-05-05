"""Shared helpers for durable session checkpoint stores."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.helpers import safe_filename

_CHECKPOINT_BASE_KEYS = frozenset({"_type", "session_key", "status", "timestamp"})


def checkpoint_path(base_dir: Path, session_key: str) -> Path:
    """Return the per-session JSONL checkpoint path for *session_key*."""
    safe_key = safe_filename(session_key.replace(":", "_"))
    return base_dir / f"{safe_key}.jsonl"


def checkpoint_metadata(row: dict[str, Any], id_key: str) -> dict[str, Any]:
    """Extract user metadata from a checkpoint row."""
    reserved = _CHECKPOINT_BASE_KEYS | {id_key}
    return {key: value for key, value in row.items() if key not in reserved}


def jsonable_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return checkpoint metadata converted to JSON-safe values."""

    def convert(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [convert(v) for v in value]
        return str(value)

    return {str(k): convert(v) for k, v in metadata.items() if v is not None}
