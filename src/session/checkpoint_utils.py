"""Shared helpers for durable session checkpoint stores."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


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
