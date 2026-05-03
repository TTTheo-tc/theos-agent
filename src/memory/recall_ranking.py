"""Recall-based candidate ranking — rank-only, no auto-promotion.

Computes 6-component weighted score for each recall target:
- frequency: log-scaled recall count
- relevance: max score observed
- diversity: min(unique queries, unique days) / 5
- recency: exponential decay from last_recalled_at (half-life 14 days)
- consolidation: unique days spread / 7 (spaced repetition)
- conceptual: placeholder (0.0 for v1, requires concept tags)

Thresholds (openclaw-inspired): score >= 0.75, recall_count >= 3,
distinct_queries >= 2.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TARGETS_REL = Path("memory") / "instinct" / "recall_targets.json"

# Weights (openclaw short-term-promotion.ts:52-58)
_W_FREQUENCY = 0.24
_W_RELEVANCE = 0.30
_W_DIVERSITY = 0.15
_W_RECENCY = 0.15
_W_CONSOLIDATION = 0.10
_W_CONCEPTUAL = 0.06

_RECENCY_HALF_LIFE_DAYS = 14.0


def _days_since(ts: str, reference: datetime | None = None) -> float:
    if not ts:
        return 999.0
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 999.0
    ref = reference or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - dt).total_seconds() / 86400.0)


def score_recall_target(
    target: dict[str, Any],
    *,
    reference_date: str | None = None,
) -> dict[str, Any]:
    """Compute 6-component score. Returns dict with 'score' and 'components'."""
    ref = None
    if reference_date:
        try:
            ref = datetime.fromisoformat(reference_date)
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            ref = None

    recall_count = int(target.get("recall_count", 0))
    query_hashes = target.get("distinct_query_hashes", [])
    days = target.get("distinct_days", [])
    max_score = float(target.get("max_score", 0.0))
    last_recalled = target.get("last_recalled_at", "")

    frequency = math.log1p(recall_count) / math.log1p(10) if recall_count > 0 else 0.0
    frequency = min(frequency, 1.0)

    relevance = min(max_score, 1.0)

    diversity = min(min(len(query_hashes), len(days)) / 5.0, 1.0)

    days_since_last = _days_since(last_recalled, ref)
    recency = math.exp(-math.log(2) * days_since_last / _RECENCY_HALF_LIFE_DAYS)

    consolidation = min(len(days) / 7.0, 1.0)

    conceptual = 0.0  # v1: no concept tags yet

    score = (
        frequency * _W_FREQUENCY
        + relevance * _W_RELEVANCE
        + diversity * _W_DIVERSITY
        + recency * _W_RECENCY
        + consolidation * _W_CONSOLIDATION
        + conceptual * _W_CONCEPTUAL
    )

    return {
        "score": round(score, 4),
        "components": {
            "frequency": round(frequency, 4),
            "relevance": round(relevance, 4),
            "diversity": round(diversity, 4),
            "recency": round(recency, 4),
            "consolidation": round(consolidation, 4),
            "conceptual": round(conceptual, 4),
        },
    }


def rank_recall_candidates(
    workspace: Path,
    *,
    min_score: float = 0.75,
    min_recall_count: int = 3,
    min_distinct_queries: int = 2,
) -> list[dict[str, Any]]:
    """Return ranked list of recall candidates. Rank-only — no side effects.

    Filters by openclaw-inspired thresholds before ranking.
    """
    targets_path = workspace / _TARGETS_REL
    if not targets_path.exists():
        return []

    try:
        targets = json.loads(targets_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    candidates: list[dict[str, Any]] = []
    for target_id, data in targets.items():
        recall_count = int(data.get("recall_count", 0))
        distinct_queries = len(data.get("distinct_query_hashes", []))
        if recall_count < min_recall_count or distinct_queries < min_distinct_queries:
            continue
        scored = score_recall_target(data)
        if scored["score"] < min_score:
            continue
        candidates.append(
            {
                "target_id": target_id,
                "score": scored["score"],
                "components": scored["components"],
                "recall_count": recall_count,
                "distinct_queries": distinct_queries,
                "distinct_days": len(data.get("distinct_days", [])),
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates
