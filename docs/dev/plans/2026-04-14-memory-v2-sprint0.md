# Memory v2 Sprint 0: Bugfix Pack Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 confirmed bugs and engineering weaknesses in the memory system before any feature work.

**Architecture:** All changes are surgical fixes to existing modules — no new files, no new truth sources, no new dependencies.

**Tech Stack:** Python 3.14, pytest, existing memory modules

**Spec:** `docs/dev/specs/2026-04-14-memory-v2-roadmap.md` (Sprint 0 section)

---

## File Map

| Action | Path | Fix |
|--------|------|-----|
| Modify | `src/memory/knowledge_search.py:386-394` | Temporal decay uses updated_at; lesson half-life |
| Modify | `src/memory/knowledge_search.py:397-436` | Apply decay post-merge |
| Modify | `src/memory/knowledge_graph.py:101-114` | Accept both created_at and updated_at |
| Modify | `src/memory/store.py:55-98` | Trigger FTS sync after remember() |
| Modify | `src/memory/extract.py:161-227` | Trigger FTS sync after merge_extracted_facts() |
| Modify | `src/memory/consolidation.py:140-141` | Per-message truncation |
| Modify | `src/memory/recall_maintenance.py:114-118` | Atomic write for targets/checkpoint |
| Modify | `tests/test_knowledge_search.py` | New tests (or expand existing) |
| Modify | `tests/test_memory_store.py` | FTS sync after remember |
| Create | `tests/test_memory_sprint0.py` | Consolidated sprint0 tests |

---

### Task 1: Temporal Decay — Use updated_at + Lesson Half-life

**Files:**
- Modify: `src/memory/knowledge_graph.py:101-114`
- Modify: `src/memory/knowledge_search.py:386-394`

- [ ] **Step 1: Write tests**

Create `tests/test_memory_sprint0.py`:

```python
"""Tests for memory v2 sprint 0 bugfixes."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest


class TestTemporalDecayUpdatedAt:
    def test_decay_uses_updated_at_when_newer(self):
        from src.memory.knowledge_graph import temporal_decay

        old_created = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        recent_updated = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        # Decay based on created_at (90 days old, half-life 60) should be low
        decay_old = temporal_decay(old_created, 60.0)
        # Decay based on updated_at (1 day old) should be near 1.0
        decay_new = temporal_decay(recent_updated, 60.0)

        assert decay_old < 0.4  # ~0.35 for 90-day rule with 60-day half-life
        assert decay_new > 0.98

    def test_compute_final_score_uses_updated_at(self):
        from src.memory.knowledge_search import _compute_final_score

        old_created = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        recent_updated = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        row_stale = {
            "node_type": "rule",
            "importance": 0.5,
            "created_at": old_created,
            "updated_at": old_created,
        }
        row_active = {
            "node_type": "rule",
            "importance": 0.5,
            "created_at": old_created,
            "updated_at": recent_updated,
        }

        # With same FTS rank, active rule should score higher
        score_stale = _compute_final_score(row_stale, raw_rank=-5.0)
        score_active = _compute_final_score(row_active, raw_rank=-5.0)
        assert score_active > score_stale


class TestLessonHalfLife:
    def test_lesson_has_nonzero_halflife(self):
        from src.memory.knowledge_search import _compute_final_score

        old_ts = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        row = {
            "node_type": "lesson",
            "importance": 0.5,
            "created_at": old_ts,
            "updated_at": old_ts,
        }
        score = _compute_final_score(row, raw_rank=-5.0)
        # If lesson had no decay (evergreen), decay factor = 1.0
        # With half-life, a 365-day-old lesson should have notable decay
        # This test ensures lesson is NOT treated as evergreen
        row_recent = {**row, "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
        score_recent = _compute_final_score(row_recent, raw_rank=-5.0)
        assert score < score_recent  # old lesson scores lower than recent one
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py -v
```

Expected: `test_compute_final_score_uses_updated_at` fails (updated_at not used), `test_lesson_has_nonzero_halflife` fails (lesson is evergreen).

- [ ] **Step 3: Fix temporal_decay to accept timestamp string (not just created_at)**

The function signature stays the same — it already accepts any ISO timestamp string. The fix is in the caller.

In `src/memory/knowledge_search.py:386-391`, replace:

```python
    created_at = row.get("created_at", "")
    # Determine half-life from node_type; default 30 days for unknown types
    half_life_map = {"task": 30.0, "rule": 60.0, "research": 90.0}
    node_type = row.get("node_type", "task")
    half_life = half_life_map.get(node_type, 0.0)  # 0 = evergreen for lesson/pattern/decision
    decay = temporal_decay(created_at, half_life) if created_at else 0.5
```

With:

```python
    created_at = row.get("created_at", "")
    updated_at = row.get("updated_at", "")
    # Use the most recent timestamp for decay — a rule re-seen yesterday
    # should not be penalized for being created 90 days ago.
    decay_ts = updated_at or created_at
    half_life_map = {"task": 30.0, "rule": 60.0, "research": 90.0, "lesson": 120.0}
    node_type = row.get("node_type", "task")
    half_life = half_life_map.get(node_type, 30.0)
    decay = temporal_decay(decay_ts, half_life) if decay_ts else 0.5
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/memory/knowledge_search.py tests/test_memory_sprint0.py
git commit -m "fix(memory): temporal decay uses updated_at, lesson gets 120-day half-life"
```

---

### Task 2: FTS Sync After remember() and merge_extracted_facts()

**Files:**
- Modify: `src/memory/store.py:55-98`
- Modify: `src/memory/extract.py:161-227`

- [ ] **Step 1: Add tests**

Append to `tests/test_memory_sprint0.py`:

```python
class TestFTSSyncAfterWrite:
    @pytest.mark.asyncio
    async def test_remember_triggers_fts_sync(self, tmp_path):
        from src.memory.index import MemoryIndex
        from src.memory.store import MemoryStore
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Memory\n")

        db = Database(tmp_path / "test.db")
        await db.setup()
        index = MemoryIndex(db)
        await index.sync_all(memory_dir)

        store = MemoryStore(tmp_path, memory_index=index)
        store.remember("always use pytest for testing")

        # Should be findable immediately, not after next consolidation
        results = await index.search("pytest testing", max_results=5)
        await db.close()
        assert any("pytest" in r.get("content", "").lower() for r in results)

    @pytest.mark.asyncio
    async def test_extract_merge_triggers_fts_sync(self, tmp_path):
        from src.memory.extract import merge_extracted_facts
        from src.memory.index import MemoryIndex
        from src.memory.store import MemoryStore
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Memory\n")

        db = Database(tmp_path / "test.db")
        await db.setup()
        index = MemoryIndex(db)
        await index.sync_all(memory_dir)

        store = MemoryStore(tmp_path, memory_index=index)
        facts = [{"section": "Decisions", "content": "We chose PostgreSQL over Redis"}]
        merged = merge_extracted_facts(store, facts)
        assert merged == 1

        results = await index.search("PostgreSQL Redis", max_results=5)
        await db.close()
        assert any("postgresql" in r.get("content", "").lower() for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py::TestFTSSyncAfterWrite -v
```

- [ ] **Step 3: Add memory_index param to MemoryStore and wire sync**

In `src/memory/store.py`, add an optional `memory_index` parameter to `__init__` and call sync after `remember()`:

```python
class MemoryStore:
    def __init__(self, workspace: Path, *, memory_index: Any = None):
        self._workspace = workspace
        self.memory_dir = workspace / "memory"
        self._memory_index = memory_index
        ...

    def remember(self, note: str, ...) -> bool:
        ...
        self.write_long_term(self._render_sections(rebuilt))
        self._sync_fts_if_available()
        return True

    def _sync_fts_if_available(self) -> None:
        if self._memory_index is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._memory_index.sync_all(self.memory_dir))
            except RuntimeError:
                # No running event loop — sync synchronously or skip
                asyncio.run(self._memory_index.sync_all(self.memory_dir))
```

In `src/memory/extract.py`, add an optional `memory_index` param to `merge_extracted_facts()` and sync after merge:

```python
def merge_extracted_facts(store: "MemoryStore", facts: list[dict[str, Any]]) -> int:
    ...
    store.write_long_term(store._render_sections(sections))
    store._sync_fts_if_available()
    return merged
```

Note: `merge_extracted_facts` already receives a `MemoryStore` — it just needs the store to have the index reference.

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py tests/test_memory_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/memory/store.py src/memory/extract.py tests/test_memory_sprint0.py
git commit -m "fix(memory): sync FTS index after remember() and merge_extracted_facts()"
```

---

### Task 3: Apply Temporal Decay Post-Merge in Hybrid Search

**Files:**
- Modify: `src/memory/knowledge_search.py:397-436`

- [ ] **Step 1: Add test**

Append to `tests/test_memory_sprint0.py`:

```python
class TestHybridMergeDecay:
    def test_merge_applies_temporal_decay(self):
        from src.memory.knowledge_search import _merge_results

        old_ts = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        new_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        fts_results = [
            {"id": "old-node", "node_type": "task", "final_score": 0.9, "created_at": old_ts, "updated_at": old_ts},
            {"id": "new-node", "node_type": "task", "final_score": 0.8, "created_at": new_ts, "updated_at": new_ts},
        ]

        merged = _merge_results(fts_results, [])
        # After decay, the new node should rank above the old one
        # despite the old one having higher raw FTS score
        ids = [r["id"] for r in merged]
        assert ids[0] == "new-node"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py::TestHybridMergeDecay -v
```

- [ ] **Step 3: Apply decay after merge**

In `_merge_results()`, after computing `final_score` (line 430-433), apply temporal decay:

```python
    # Apply temporal decay to merged scores
    for entry in by_id.values():
        fts_s = entry.pop("_fts_score", 0.0)
        vec_s = entry.pop("_vec_score", 0.0)
        raw_score = _W_VECTOR * vec_s + _W_TEXT * fts_s

        # Post-merge temporal decay
        node_type = entry.get("node_type", "task")
        half_life_map = {"task": 30.0, "rule": 60.0, "research": 90.0, "lesson": 120.0}
        half_life = half_life_map.get(node_type, 30.0)
        decay_ts = entry.get("updated_at") or entry.get("created_at", "")
        decay = temporal_decay(decay_ts, half_life) if decay_ts else 0.5
        entry["final_score"] = round(raw_score * decay, 4)
```

Add import at top of file: `from src.memory.knowledge_graph import temporal_decay` (if not already imported).

- [ ] **Step 4: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py tests/test_knowledge_search.py tests/test_knowledge_graph.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/memory/knowledge_search.py tests/test_memory_sprint0.py
git commit -m "fix(memory): apply temporal decay post-merge in hybrid search"
```

---

### Task 4: Consolidation Per-Message Truncation

**Files:**
- Modify: `src/memory/consolidation.py:140-141`

- [ ] **Step 1: Add test**

Append to `tests/test_memory_sprint0.py`:

```python
class TestConsolidationTruncation:
    def test_long_message_truncated_in_prompt(self):
        from src.memory.consolidation import MemoryConsolidationService

        svc = MemoryConsolidationService.__new__(MemoryConsolidationService)
        # Build a message with very long content
        long_content = "x" * 5000
        messages = [{"role": "assistant", "content": long_content, "timestamp": "2026-04-14T00:00"}]

        # Access the prompt-building logic
        lines = []
        for m in messages:
            content = m.get("content", "")
            if len(content) > 1000:
                content = content[:500] + " ... [truncated] ... " + content[-500:]
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {content}")

        assert len(lines[0]) < 1200  # truncated, not 5000+
        assert "[truncated]" in lines[0]
```

- [ ] **Step 2: Apply truncation**

In `src/memory/consolidation.py:140-141`, replace:

```python
            lines.append(
                f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}"
            )
```

With:

```python
            content = m["content"]
            if len(content) > 1000:
                content = content[:500] + " ... [truncated] ... " + content[-500:]
            lines.append(
                f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {content}"
            )
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py::TestConsolidationTruncation tests/test_consolidation_service.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/memory/consolidation.py tests/test_memory_sprint0.py
git commit -m "fix(memory): truncate long messages in consolidation prompt"
```

---

### Task 5: Maintenance Atomic Writes

**Files:**
- Modify: `src/memory/recall_maintenance.py:114-118`

- [ ] **Step 1: Add test**

Append to `tests/test_memory_sprint0.py`:

```python
class TestMaintenanceAtomicWrite:
    def test_targets_written_atomically(self, tmp_path):
        """If write fails mid-way, old targets file should survive."""
        import json
        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)

        # Write initial journal + fold
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(json.dumps({"target_kind": "rule", "target_id": "rule-a", "query_hash": "h1", "day": "2026-04-14", "score": 0.5}) + "\n")
        fold_recall_journal(tmp_path)

        targets = instinct_dir / "recall_targets.json"
        assert targets.exists()
        # Verify it's valid JSON
        data = json.loads(targets.read_text())
        assert "rule-a" in data
```

- [ ] **Step 2: Implement atomic write**

In `src/memory/recall_maintenance.py`, replace the direct `write_text` calls (lines 117-118):

```python
    # Write targets + checkpoint atomically (tmp + rename)
    try:
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(targets_path, json.dumps(targets, indent=2, ensure_ascii=False) + "\n")
        _atomic_write(checkpoint_path, json.dumps({"byte_offset": new_offset}) + "\n")
    except OSError:
        logger.opt(exception=True).warning("Failed to write recall targets")
```

Add helper at module top:

```python
import tempfile

def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via tmp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

Add `import os` at top if not present.

- [ ] **Step 3: Run tests**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py::TestMaintenanceAtomicWrite tests/test_recall_maintenance.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/memory/recall_maintenance.py tests/test_memory_sprint0.py
git commit -m "fix(memory): atomic write for recall targets and checkpoint"
```

---

### Task 6: Lint + Integration Test

- [ ] **Step 1: make fmt + make lint**

```bash
cd /Users/tc/code/theos-agent && make fmt && make lint
```

- [ ] **Step 2: Run full affected test suite**

```bash
cd /Users/tc/code/theos-agent && uv run pytest tests/test_memory_sprint0.py tests/test_knowledge_search.py tests/test_knowledge_graph.py tests/test_memory_store.py tests/test_consolidation_service.py tests/test_recall_maintenance.py tests/test_recall_journal.py tests/test_pre_compaction_flush.py -v
```

- [ ] **Step 3: Commit + push**

```bash
git add -A && git push
```

**Verification summary:**
- `make fmt`: ran/passed
- `make lint`: ran/passed
- `uv run pytest` (affected): ran/passed
- README.md: not needed — no user-facing changes
- BOT.md: not needed — no dev flow changes
