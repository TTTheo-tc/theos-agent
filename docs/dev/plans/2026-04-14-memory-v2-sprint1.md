# Memory v2 Sprint 1: Retrieval Quality Implementation Plan

**Goal:** 让 memory retrieval 更细、更准、更省 context。

**Architecture:** 扩展现有检索模块，不新增存储层。复用 FTS5 index 和 KG search 基础设施。

**Tech Stack:** Python 3.14, pytest, existing memory modules

**Spec:** `docs/dev/specs/2026-04-14-memory-v2-roadmap.md` Sprint 1

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/memory/mmr.py` | MMR re-ranking helper |
| Modify | `src/memory/knowledge_search.py` | Apply MMR post-merge |
| Modify | `src/memory/recall.py:140-203` | Upgrade section scoring + graded fallback |
| Modify | `src/agent/tools/memory_search.py:304-362` | `memory_get` supports line ranges |
| Create | `tests/test_memory_sprint1.py` | Sprint 1 tests |

---

### Task 1.1: MMR Re-ranking

**Files:**
- Create: `src/memory/mmr.py`
- Modify: `src/memory/knowledge_search.py`

- [ ] **Step 1: Write tests**

Create `tests/test_memory_sprint1.py`:

```python
"""Sprint 1: Retrieval quality tests."""
from __future__ import annotations

import pytest


class TestMMR:
    def test_mmr_prefers_diverse_results(self):
        from src.memory.mmr import mmr_rerank

        results = [
            {"id": "a", "content": "python testing with pytest", "final_score": 0.9},
            {"id": "b", "content": "python testing with pytest framework", "final_score": 0.85},
            {"id": "c", "content": "rust async programming", "final_score": 0.7},
        ]
        reranked = mmr_rerank(results, k=3, lambda_=0.5)
        ids = [r["id"] for r in reranked]
        # a should be first (highest relevance)
        # c should come before b because b is nearly identical to a
        assert ids[0] == "a"
        assert ids[1] == "c"

    def test_mmr_respects_k_limit(self):
        from src.memory.mmr import mmr_rerank

        results = [
            {"id": f"r{i}", "content": f"unique content {i}", "final_score": 0.5 + i * 0.1}
            for i in range(5)
        ]
        reranked = mmr_rerank(results, k=3, lambda_=0.7)
        assert len(reranked) == 3

    def test_mmr_handles_empty(self):
        from src.memory.mmr import mmr_rerank

        assert mmr_rerank([], k=3) == []

    def test_mmr_handles_single(self):
        from src.memory.mmr import mmr_rerank

        result = mmr_rerank([{"id": "a", "content": "x", "final_score": 0.5}], k=3)
        assert len(result) == 1
```

- [ ] **Step 2: Implement MMR**

Create `src/memory/mmr.py`:

```python
"""Maximal Marginal Relevance re-ranking for search results.

Balances relevance against diversity: each selected item maximizes
`lambda * relevance - (1 - lambda) * max_similarity_to_selected`.

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
            tokens.add(chunk[i:i + 2])
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

    lambda_=1.0 pure relevance, lambda_=0.0 pure diversity.
    """
    if not results:
        return []
    if len(results) <= 1:
        return list(results[:k])

    # Normalize scores to [0, 1]
    scores = [r.get("final_score", 0.0) for r in results]
    max_score = max(scores) if scores else 1.0
    if max_score <= 0:
        max_score = 1.0
    normalized = [s / max_score for s in scores]

    tokens = [_tokenize(str(r.get("content", "") or r.get("title", "") or r.get("summary", ""))) for r in results]

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
            max_sim = max(_jaccard(tokens[i], tokens[j]) for j in selected_idx) if selected_idx else 0.0
            mmr = lambda_ * relevance - (1 - lambda_) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        if best_idx < 0:
            break
        selected_idx.append(best_idx)
        remaining.remove(best_idx)

    return [results[i] for i in selected_idx]
```

- [ ] **Step 3: Wire MMR into hybrid_search**

In `src/memory/knowledge_search.py`, modify `hybrid_search()` to apply MMR after merge. Find the end of `hybrid_search` method and add MMR call before returning.

- [ ] **Step 4: Run tests, fmt, lint, commit**

---

### Task 1.2: Upgrade Pre-turn Recall Section Scoring

**Files:**
- Modify: `src/memory/recall.py:140-203`

- [ ] **Step 1: Tests**

Add to `tests/test_memory_sprint1.py`:

```python
class TestRecallSectionScoring:
    def test_stop_words_excluded_from_overlap(self):
        """Common stop words shouldn't inflate overlap scores."""
        from src.memory.recall import _score_section

        # Both sections match "the" but one is genuinely more relevant
        section_relevant = ("Architecture", "the postgres database migration plan")
        section_noise = ("Random", "the the the the quick brown fox")
        query = "postgres migration"

        score_rel = _score_section(section_relevant, query)
        score_noise = _score_section(section_noise, query)
        assert score_rel > score_noise

    def test_section_length_normalized(self):
        """Long sections shouldn't win just because they contain more words."""
        from src.memory.recall import _score_section

        # Short section with exact match
        short_section = ("Decision", "use postgres for state")
        # Very long section that happens to contain "postgres" once
        long_section = ("Random", " ".join(["filler"] * 200) + " postgres " + " ".join(["noise"] * 200))
        query = "postgres"

        short_score = _score_section(short_section, query)
        long_score = _score_section(long_section, query)
        assert short_score > long_score


class TestGradedFallback:
    def test_pinned_sections_prefered_fallback(self, tmp_path):
        """When no matches, pinned sections are fallback before full dump."""
        from src.memory.recall import _graded_fallback

        sections = [
            ("_preamble", "# Memory"),
            ("Decisions", "<!-- pinned -->\nalways use pytest"),
            ("Old Notes", "something unrelated"),
        ]
        result = _graded_fallback(sections, budget_chars=200)
        assert "pytest" in result  # pinned section wins
```

- [ ] **Step 2: Implement scoring helpers**

In `src/memory/recall.py`, replace the simple overlap scoring with BM25-lite and add graded fallback:

```python
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "and", "or", "but", "not", "this", "that", "these", "those",
    "it", "its", "i", "you", "we", "they", "he", "she",
    "do", "does", "did", "have", "has", "had", "will", "would",
    "can", "could", "should", "may", "might", "must",
})


def _tokenize_for_score(text: str) -> list[str]:
    tokens = re.findall(r"\w+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _score_section(section: tuple[str, str], query: str) -> float:
    """Score section relevance with length normalization + title boost."""
    title, body = section
    query_tokens = set(_tokenize_for_score(query))
    if not query_tokens:
        return 0.0

    title_tokens = set(_tokenize_for_score(title))
    body_tokens = _tokenize_for_score(body)

    if not body_tokens and not title_tokens:
        return 0.0

    # Title match is 2x weighted
    title_hits = len(query_tokens & title_tokens) * 2.0

    # Body match with length normalization (prevents long sections from dominating)
    body_set = set(body_tokens)
    body_hits = len(query_tokens & body_set)
    body_len = max(len(body_tokens), 10)  # avoid zero-division and tiny-body inflation
    normalized_body = body_hits / (body_len ** 0.5) * 10.0  # BM25-like normalization

    return title_hits + normalized_body


def _graded_fallback(sections: list[tuple[str, str]], budget_chars: int) -> str:
    """Fallback when no sections match: try pinned first, then recent, then full."""
    pinned: list[tuple[str, str]] = []
    for title, body in sections:
        if title == "_preamble":
            continue
        if "<!-- pinned" in body:
            pinned.append((title, body))

    if pinned:
        selected: list[str] = []
        used = 0
        for title, body in pinned:
            txt = f"## {title}\n{body}"
            if used + len(txt) > budget_chars and selected:
                break
            selected.append(txt)
            used += len(txt)
        return "## Long-term Memory (pinned fallback)\n" + "\n\n".join(selected)

    return ""  # caller decides whether to full-dump
```

Then update `_select_markdown_sections()` to use `_score_section()` and call `_graded_fallback()` before falling back to full.

- [ ] **Step 3: Run tests, fmt, lint, commit**

---

### Task 1.3: memory_get Fine-Grained Reading

**Files:**
- Modify: `src/agent/tools/memory_search.py:304-362`

- [ ] **Step 1: Tests**

Add to `tests/test_memory_sprint1.py`:

```python
class TestMemoryGetLines:
    @pytest.mark.asyncio
    async def test_memory_get_supports_line_range(self, tmp_path):
        """memory_get should support retrieving line N-M of a section."""
        from src.memory.index import MemoryIndex
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text(
            "# Memory\n## Decisions\n- fact 1\n- fact 2\n- fact 3\n- fact 4\n- fact 5\n"
        )

        db = Database(tmp_path / "test.db")
        await db.setup()
        index = MemoryIndex(db)
        await index.sync_all(memory_dir)

        # Simulate tool invocation with lines range
        from src.agent.tools.memory_search import MemoryGetTool

        tool = MemoryGetTool(index_resolver=lambda _sk: index)
        # Mock context
        from src.agent.tools.context import ToolContext
        ctx = ToolContext(session_key="cli:test")
        result = await tool.execute(_context=ctx, section="Decisions", from_line=2, lines=2)
        await db.close()
        # Should only get 2 lines starting at line 2
        assert "fact 2" in result or "fact 3" in result
```

- [ ] **Step 2: Implement line range support**

In `src/agent/tools/memory_search.py`, extend `MemoryGetTool`:

- Add `from_line: int | None` and `lines: int | None` to parameters schema
- After fetching content, if `from_line`/`lines` set, slice the lines

```python
    async def execute(self, _context: "ToolContext | None" = None, **kwargs: Any) -> str:
        ...
        content = await index.get_section(section)
        if not content:
            return f"Section '{section}' not found in memory."

        # Optional line range slicing
        from_line = kwargs.get("from_line")
        lines_count = kwargs.get("lines")
        if from_line is not None or lines_count is not None:
            all_lines = content.split("\n")
            start = max(0, (from_line or 1) - 1)
            end = start + lines_count if lines_count else len(all_lines)
            content = "\n".join(all_lines[start:end])

        return content
```

- [ ] **Step 3: Tests, fmt, lint, commit**

---

### Task 1.4: Integration + Push

- [ ] **Step 1: Run full suite**
- [ ] **Step 2: make fmt + make lint**
- [ ] **Step 3: Push**
