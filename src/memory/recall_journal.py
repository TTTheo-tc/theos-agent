"""Recall journal — append-only telemetry for memory search events.

Each memory_search / structured_memory_search / domain_rule_get call
appends one line per result to recall_journal.jsonl.  This is the sole
hot-path write; the derived recall_targets.json is built offline by
recall_maintenance.py.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.memory.memory_events import append_memory_event

_JOURNAL_REL = Path("memory") / "instinct" / "recall_journal.jsonl"


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.lower().strip().encode()).hexdigest()[:12]


def _claim_hash(content: str) -> str:
    """SHA1[:12] of normalized content — stable identity for a claim string."""
    normalized = " ".join(content.lower().split())
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


def _entry_for_result(
    result: dict[str, Any],
    *,
    timestamp: str,
    session_key: str | None,
    tool: str,
    query: str,
    query_hash: str,
    day: str,
) -> dict[str, Any]:
    content = result.get("content")
    return {
        "timestamp": timestamp,
        "session_key": session_key or "",
        "tool": tool,
        "query": query,
        "query_hash": query_hash,
        "day": day,
        "target_kind": result.get("target_kind", ""),
        "target_id": result.get("target_id"),
        "path": result.get("path", ""),
        "score": result.get("score"),
        "domains": result.get("domains", []),
        "claim_hash": _claim_hash(content) if content else None,
    }


def _event_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_kind": result.get("target_kind", ""),
        "target_id": result.get("target_id"),
        "path": result.get("path", ""),
        "score": result.get("score"),
    }


async def append_recall_entries(
    *,
    workspace: Path,
    session_key: str | None,
    tool: str,
    query: str,
    results: list[dict[str, Any]],
) -> None:
    """Append one JSONL line per result to the recall journal.

    Best-effort: catches all exceptions internally, never raises.
    """
    if not results:
        return

    try:
        journal_path = workspace / _JOURNAL_REL
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        ts = now.isoformat()
        day = now.strftime("%Y-%m-%d")
        qhash = _query_hash(query)

        lines = [
            json.dumps(
                _entry_for_result(
                    r,
                    timestamp=ts,
                    session_key=session_key,
                    tool=tool,
                    query=query,
                    query_hash=qhash,
                    day=day,
                ),
                ensure_ascii=False,
            )
            for r in results
        ]

        with open(journal_path, "a") as f:
            f.write("\n".join(lines) + "\n")

        append_memory_event(
            workspace=workspace,
            event_type="memory.recall.recorded",
            payload={
                "tool": tool,
                "query": query,
                "result_count": len(results),
                "results": [_event_result(r) for r in results],
            },
        )

    except Exception:
        logger.opt(exception=True).debug("Failed to write recall journal entry")
