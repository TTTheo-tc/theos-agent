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

        lines: list[str] = []
        for r in results:
            content = r.get("content")
            entry = {
                "timestamp": ts,
                "session_key": session_key or "",
                "tool": tool,
                "query": query,
                "query_hash": qhash,
                "day": day,
                "target_kind": r.get("target_kind", ""),
                "target_id": r.get("target_id"),
                "path": r.get("path", ""),
                "score": r.get("score"),
                "domains": r.get("domains", []),
                "claim_hash": _claim_hash(content) if content else None,
            }
            lines.append(json.dumps(entry, ensure_ascii=False))

        with open(journal_path, "a") as f:
            f.write("\n".join(lines) + "\n")

        append_memory_event(
            workspace=workspace,
            event_type="memory.recall.recorded",
            payload={
                "tool": tool,
                "query": query,
                "result_count": len(results),
                "results": [
                    {
                        "target_kind": r.get("target_kind", ""),
                        "target_id": r.get("target_id"),
                        "path": r.get("path", ""),
                        "score": r.get("score"),
                    }
                    for r in results
                ],
            },
        )

    except Exception:
        logger.opt(exception=True).debug("Failed to write recall journal entry")
