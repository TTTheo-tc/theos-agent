"""JSON object coercion helpers for memory stores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def coerce_json_object(value: Any) -> dict[str, Any]:
    """Return a dict from a mapping or JSON object string; reject other shapes."""
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def read_json_object(path: Path) -> tuple[dict[str, Any], bool]:
    """Read a JSON object from disk, returning ``({}, False)`` on invalid input."""
    if not path.exists():
        return {}, False
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}, False
    return (dict(parsed), True) if isinstance(parsed, dict) else ({}, False)
