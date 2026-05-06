"""Maximal Marginal Relevance re-ranking for search results.

Balances relevance against diversity: each selected item maximizes
``lambda * relevance - (1 - lambda) * max_similarity_to_selected``.

Uses Jaccard similarity on word token sets for similarity estimation.
"""

from __future__ import annotations

import re
from typing import Any


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, minimal CJK support."""
    if not text:
        return set()
    tokens = set(re.findall(r"\w+", text.lower()))
    # CJK bigrams
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        chunk = match.group(0)
        for i in range(len(chunk) - 1):
            tokens.add(chunk[i : i + 2])
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def mmr_rerank(
    results: list[dict[str, Any]],
    k: int,
    lambda_: float = 0.7,
) -> list[dict[str, Any]]:
    """Re-rank results using MMR; return top k with diversity.

    ``lambda_=1.0`` is pure relevance, ``lambda_=0.0`` is pure diversity.
    """
    if not results or k <= 0:
        return []
    if len(results) <= 1:
        return list(results[:k])

    # Normalize scores to [0, 1]
    # Accept "final_score" (knowledge_search path) or "score" (StructuredMemoryStore path)
    scores = [float(r.get("final_score", r.get("score", 0.0)) or 0.0) for r in results]
    max_score = max(scores) if scores else 1.0
    if max_score <= 0:
        max_score = 1.0
    normalized = [s / max_score for s in scores]

    tokens = [
        _tokenize(str(r.get("content", "") or r.get("title", "") or r.get("summary", "")))
        for r in results
    ]

    selected_idx: list[int] = []
    remaining = set(range(len(results)))

    # First pick: highest relevance
    first = max(remaining, key=lambda i: normalized[i])
    selected_idx.append(first)
    remaining.remove(first)

    while remaining and len(selected_idx) < k:
        best_idx = -1
        best_score = -float("inf")
        for i in remaining:
            relevance = normalized[i]
            max_sim = (
                max(_jaccard(tokens[i], tokens[j]) for j in selected_idx) if selected_idx else 0.0
            )
            mmr = lambda_ * relevance - (1 - lambda_) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        if best_idx < 0:
            break
        selected_idx.append(best_idx)
        remaining.remove(best_idx)

    return [results[i] for i in selected_idx]
