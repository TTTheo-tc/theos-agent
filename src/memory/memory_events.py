"""Unified memory event log — append-only observability channel.

Every memory subsystem (fold, ingest, flush, promotion, etc.) emits events
here so downstream tooling has a single JSONL stream to consume. Best-effort:
failures are swallowed so callers never raise due to observability.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_EVENTS_REL = Path("memory") / "instinct" / "memory_events.jsonl"


def append_memory_event(
    *,
    workspace: Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a memory event to the unified log. Best-effort, never raises."""
    try:
        path = workspace / _EVENTS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "payload": payload or {},
        }
        with open(path, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        logger.opt(exception=True).debug("Failed to append memory event")
