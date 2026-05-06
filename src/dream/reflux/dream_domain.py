"""Dream content retrieval for L1/L2 reflux.

Python-side lookup — used for diagnostics and future L2 integration.
The hot-path L1 query runs in reflex.js (no cross-runtime call).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DREAM_REFLUX_LEVEL = os.environ.get("INSTINCT_DREAM_REFLUX_LEVEL", "L0")


@dataclass
class DreamHint:
    """A single dream retrieval result."""

    session_id: str
    topic: str
    summary: str
    insights: list[str]
    review_path: str
    score: float = 0.0


def dream_lookup(
    topic: str,
    workspace: Path,
    max_results: int = 3,
    min_score: float = 5.0,
) -> list[DreamHint]:
    """Search DREAM_INDEX.jsonl for sessions relevant to topic.
    Falls back to scanning dreams/ directories if index is missing.
    """
    if DREAM_REFLUX_LEVEL == "L0":
        return []
    index_path = workspace / "memory" / "instinct" / "DREAM_INDEX.jsonl"
    if index_path.exists():
        return _lookup_from_index(topic, index_path, max_results, min_score)
    return _lookup_from_dirs(topic, workspace, max_results, min_score)


def _lookup_from_index(
    topic: str,
    index_path: Path,
    max_results: int,
    min_score: float,
) -> list[DreamHint]:
    topic_lower = topic.lower()
    topic_words = {w for w in topic_lower.split() if len(w) > 2}
    results: list[DreamHint] = []
    try:
        for line in index_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("status") == "failed":
                continue
            score = 0.0
            entry_topic = (entry.get("topic") or "").lower()
            if topic_lower in entry_topic or entry_topic in topic_lower:
                score += 10
            for tag in entry.get("tags") or []:
                if tag.lower() in topic_lower:
                    score += 3
            summary_words = (entry.get("summary") or "").lower().split()
            for w in summary_words:
                if len(w) > 2 and w in topic_words:
                    score += 1
            if entry.get("reviewed_by_user"):
                score += 2
            if score >= min_score:
                results.append(
                    DreamHint(
                        session_id=entry["session_id"],
                        topic=entry.get("topic", ""),
                        summary=entry.get("summary", ""),
                        insights=(entry.get("insights") or [])[:3],
                        review_path=entry.get("review_path", ""),
                        score=score,
                    )
                )
    except Exception:
        return []
    results.sort(key=lambda h: h.score, reverse=True)
    return results[:max_results]


def _lookup_from_dirs(
    topic: str,
    workspace: Path,
    max_results: int,
    min_score: float,
) -> list[DreamHint]:
    """Fallback: scan dreams/*/dream_eval.json when index is missing."""
    dreams_dir = workspace / "memory" / "instinct" / "dreams"
    if not dreams_dir.exists():
        return []
    topic_lower = topic.lower()
    results: list[DreamHint] = []
    for session_dir in sorted(dreams_dir.iterdir(), reverse=True)[:50]:
        eval_path = session_dir / "dream_eval.json"
        if not eval_path.exists():
            continue
        try:
            data = json.loads(eval_path.read_text())
            if data.get("status") == "failed":
                continue
            entry_topic = (data.get("topic") or "").lower()
            score = 10.0 if (topic_lower in entry_topic or entry_topic in topic_lower) else 0.0
            if score >= min_score:
                results.append(
                    DreamHint(
                        session_id=data.get("session_id", session_dir.name),
                        topic=data.get("topic", ""),
                        summary=f"Dream session: {data.get('topic', '')}",
                        insights=[],
                        review_path=str(session_dir / "dream-review.md"),
                        score=score,
                    )
                )
        except Exception:
            continue
    results.sort(key=lambda h: h.score, reverse=True)
    return results[:max_results]


# Legacy stubs kept for backward compat — L2 only
def get_dream_context(topic: str) -> dict[str, Any] | None:
    del topic
    if DREAM_REFLUX_LEVEL != "L2":
        return None
    return None


def list_recent_dreams(limit: int = 5) -> list[dict[str, Any]]:
    del limit
    if DREAM_REFLUX_LEVEL != "L2":
        return []
    return []


def get_dream_insights(session_id: str) -> list[str]:
    del session_id
    if DREAM_REFLUX_LEVEL != "L2":
        return []
    return []
