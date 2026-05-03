# Memory v2 Sprint 2: Recall Intelligence Implementation Plan

**Goal:** Turn v1 recall telemetry into rankable signals; add unified event log; produce rank-only candidates (no auto-promotion).

**Architecture:** Extend existing `recall_journal.jsonl` / `recall_targets.json` schema. Add unified event log alongside existing artifacts. No KG schema changes. No changes to instinct promotion pipeline.

**Tech Stack:** Python 3.14, pytest

**Spec:** `docs/dev/specs/2026-04-14-memory-v2-roadmap.md` Sprint 2

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/memory/recall_journal.py` | Add claim_hash support to entries |
| Modify | `src/memory/recall_maintenance.py` | Fold new schema: total_score, daily_count |
| Create | `src/memory/recall_ranking.py` | 6-component scoring (rank-only) |
| Create | `src/memory/memory_events.py` | Unified event log |
| Modify | `src/memory/recall_maintenance.py` | Emit events during fold/ingest |
| Modify | `src/agent/loop_memory.py` (_write_flush_event) | Also write to unified event log |
| Create | `tests/test_memory_sprint2.py` | Sprint 2 tests |

---

### Task 2.1: Extend Recall Schema

**Files:**
- Modify: `src/memory/recall_journal.py`
- Modify: `src/memory/recall_maintenance.py`

New fields in journal entry (all optional, backward-compatible):
- `claim_hash`: SHA1[:12] of normalized content (for grounding)

New fields aggregated in `recall_targets.json`:
- `total_score`: cumulative score sum
- `daily_count`: max recalls in a single day (consolidation signal)

New fields in `append_recall_entries()` — `results` items can include:
- `content` (optional): will be hashed to `claim_hash`

Backward compat: if `content` not provided, `claim_hash` omitted.

- [ ] **Step 1: Tests**

```python
class TestRecallSchemaExtension:
    @pytest.mark.asyncio
    async def test_claim_hash_written_when_content_present(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries
        import json

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[{
                "target_kind": "kg_rule",
                "target_id": "rule-abc",
                "content": "always use pytest",
                "score": 0.9,
                "domains": [],
            }],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert entry.get("claim_hash")
        assert len(entry["claim_hash"]) == 12

    @pytest.mark.asyncio
    async def test_claim_hash_omitted_when_no_content(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries
        import json

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[{"target_kind": "kg_rule", "target_id": "rule-xyz", "score": 0.5, "domains": []}],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        # claim_hash should be null/missing when no content
        assert entry.get("claim_hash") is None or entry.get("claim_hash") == ""

    def test_fold_aggregates_total_score_and_daily_count(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal
        import json

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        lines = [
            json.dumps({"target_kind": "kg_rule", "target_id": "rule-a", "query_hash": "h1", "day": "2026-04-14", "score": 0.5}),
            json.dumps({"target_kind": "kg_rule", "target_id": "rule-a", "query_hash": "h2", "day": "2026-04-14", "score": 0.6}),
            json.dumps({"target_kind": "kg_rule", "target_id": "rule-a", "query_hash": "h3", "day": "2026-04-15", "score": 0.7}),
        ]
        journal.write_text("\n".join(lines) + "\n")

        fold_recall_journal(tmp_path)
        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        t = targets["rule-a"]
        assert abs(t["total_score"] - 1.8) < 0.001
        # daily_count: peak = 2 (two entries on 2026-04-14)
        assert t["daily_count"] == 2
```

- [ ] **Step 2: Implementation**

In `src/memory/recall_journal.py`, add `claim_hash` to each entry:

```python
import hashlib

def _claim_hash(content: str) -> str:
    normalized = " ".join(content.lower().split())
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]

# In append_recall_entries:
for r in results:
    entry = {
        ...
        "claim_hash": _claim_hash(r["content"]) if r.get("content") else None,
    }
```

In `src/memory/recall_maintenance.py`, extend `_fold_recall_journal_locked()` to aggregate:

```python
# In targets[tid] init:
"total_score": 0.0,
"daily_count": 0,
"daily_counts": {},  # per-day counter (derived)

# Accumulation:
score = entry.get("score") or 0
t["total_score"] += score
day = entry.get("day", "")
if day:
    t["daily_counts"][day] = t["daily_counts"].get(day, 0) + 1
    t["daily_count"] = max(t["daily_count"], t["daily_counts"][day])
```

---

### Task 2.2: Unified Memory Event Log

**Files:**
- Create: `src/memory/memory_events.py`
- Modify: `src/memory/recall_maintenance.py` (emit events)
- Modify: `src/agent/loop_memory.py` (flush event also goes to unified log)

- [ ] **Step 1: Tests**

```python
class TestUnifiedEventLog:
    def test_append_memory_event(self, tmp_path):
        from src.memory.memory_events import append_memory_event
        import json

        append_memory_event(
            workspace=tmp_path,
            event_type="memory.recall.folded",
            payload={"targets_updated": 3},
        )
        events_path = tmp_path / "memory" / "instinct" / "memory_events.jsonl"
        assert events_path.exists()
        entry = json.loads(events_path.read_text().strip())
        assert entry["type"] == "memory.recall.folded"
        assert entry["payload"]["targets_updated"] == 3
        assert entry["timestamp"]

    def test_fold_emits_event(self, tmp_path):
        import json
        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(json.dumps({"target_kind": "kg_rule", "target_id": "rule-x", "query_hash": "h1", "day": "2026-04-14", "score": 0.5}) + "\n")

        fold_recall_journal(tmp_path)

        events = instinct_dir / "memory_events.jsonl"
        assert events.exists()
        lines = [json.loads(l) for l in events.read_text().strip().split("\n") if l]
        assert any(e["type"] == "memory.recall.folded" for e in lines)
```

- [ ] **Step 2: Implementation**

Create `src/memory/memory_events.py`:

```python
"""Unified memory event log — append-only observability channel."""
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
```

Wire emissions in recall_maintenance.py:
- After successful fold: `append_memory_event(workspace, "memory.recall.folded", {"targets_updated": count})`
- After successful ingest: `append_memory_event(workspace, "memory.recall.ingested", {"rules_updated": count})`

Wire in loop_memory._write_flush_event:
- Also call `append_memory_event(workspace, "memory.flush.completed", {"facts_merged": facts_merged})`

---

### Task 2.3: Recall-Based Ranking (Rank-Only)

**Files:**
- Create: `src/memory/recall_ranking.py`
- Create test in `tests/test_memory_sprint2.py`

6-component score adapted from openclaw, using v1+v2.1 signals:
- frequency: `log1p(recall_count) / log1p(10) * 0.24`
- relevance: `max_score * 0.30`
- diversity: `min(distinct_query_count, distinct_day_count) / 5 * 0.15`
- recency: `exp(-ln2 * days_since_last / 14) * 0.15`
- consolidation: `min(distinct_days / 7, 1.0) * 0.10`
- conceptual: 0 for v1 (no concept tags yet) * 0.06

Threshold: only return candidates with score >= 0.75 AND recall_count >= 3 AND distinct_queries >= 2.

**Critical: rank-only output. Do NOT automatically write to instinct ACTIVE.md or KG promotion.**

- [ ] **Step 1: Tests**

```python
class TestRecallRanking:
    def test_score_components_and_threshold(self):
        from src.memory.recall_ranking import score_recall_target

        target = {
            "recall_count": 5,
            "distinct_query_hashes": ["h1", "h2", "h3"],
            "distinct_days": ["2026-04-10", "2026-04-11", "2026-04-12"],
            "last_recalled_at": "2026-04-14T00:00:00",
            "max_score": 0.9,
            "total_score": 4.0,
            "daily_count": 2,
        }
        score = score_recall_target(target, reference_date="2026-04-14")
        assert score > 0.0
        assert score <= 1.0

    def test_rank_candidates_applies_threshold(self, tmp_path):
        from src.memory.recall_ranking import rank_recall_candidates
        import json

        targets = {
            "rule-strong": {
                "recall_count": 5,
                "distinct_query_hashes": ["h1", "h2", "h3"],
                "distinct_days": ["2026-04-10", "2026-04-11"],
                "last_recalled_at": "2026-04-14T00:00:00",
                "max_score": 0.95,
                "total_score": 4.5,
                "daily_count": 2,
            },
            "rule-weak": {
                "recall_count": 1,  # below threshold
                "distinct_query_hashes": ["h1"],
                "distinct_days": ["2026-04-14"],
                "last_recalled_at": "2026-04-14T00:00:00",
                "max_score": 0.5,
                "total_score": 0.5,
                "daily_count": 1,
            },
        }
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.parent.mkdir(parents=True)
        targets_path.write_text(json.dumps(targets))

        candidates = rank_recall_candidates(tmp_path)
        # rule-weak excluded by threshold; rule-strong included
        ids = [c["target_id"] for c in candidates]
        assert "rule-strong" in ids
        assert "rule-weak" not in ids
```

- [ ] **Step 2: Implementation**

Create `src/memory/recall_ranking.py` with:
- `score_recall_target(target, reference_date)` → float
- `rank_recall_candidates(workspace, min_score=0.75)` → list of `{target_id, score, components, ...}`

---

### Task 2.4: Lint + Push
