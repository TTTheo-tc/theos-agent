# Memory v2 Sprint 3: Safety + Bridge Implementation Plan

**Goal:** Prevent catastrophic memory loss (consolidation validation), add bidirectional instinct↔KG bridge, add KG GC.

**Architecture:** Safety-first. Consolidation output validation is highest priority — a single bad LLM consolidation can wipe MEMORY.md. Everything else depends on this being solid.

**Spec:** `docs/dev/specs/2026-04-14-memory-v2-roadmap.md` Sprint 3

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/memory/consolidation.py` | Output validation (section count, pinned protection, format) |
| Modify | `src/memory/structured.py` | KG GC — delete old superseded nodes, VACUUM |
| Modify | `instinct/scripts/evolve.js` | Read KG high-conf rules as additional input |
| Create | `tests/test_memory_sprint3.py` | Sprint 3 tests |

---

### Task 3.1: Consolidation Output Validation

**Files:**
- Modify: `src/memory/consolidation.py:240-290`

Validation pipeline (reject bad output, log warning, keep old memory):

1. **Format check**: output must contain at least one `## ` section header
2. **Section count sanity**: new section count >= max(3, old_count * 0.5) — reject if LLM dropped too many sections
3. **Pinned section protection**: all sections that had `<!-- pinned -->` in old memory must still be present (by title) in new memory
4. **Size sanity**: output length should be in range [0.3x, 3x] of current memory — reject if suspiciously small or explosively large

Rejection behavior:
- Log warning with reason
- Fall back to `_build_fallback_history_entry` (keep old memory, just archive conversation)
- Do NOT crash the consolidation pipeline

- [ ] **Step 1: Tests**

```python
class TestConsolidationValidation:
    def test_rejects_output_dropping_pinned_section(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## Projects\n<!-- pinned -->\n- TheOS\n## Notes\n- misc"
        new = "# Memory\n## Notes\n- misc"  # dropped pinned Projects section!
        valid, reason = _validate_memory_update(old, new)
        assert not valid
        assert "pinned" in reason.lower()

    def test_rejects_massive_section_drop(self):
        from src.memory.consolidation import _validate_memory_update

        old = "\n".join([f"## Section{i}\n- content" for i in range(10)])
        new = "## Section1\n- content"  # dropped 9/10 sections
        valid, reason = _validate_memory_update(old, new)
        assert not valid

    def test_rejects_explosive_size(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## One\n- x"
        new = "# Memory\n" + "## Section\n- x\n" * 1000  # 1000x larger
        valid, reason = _validate_memory_update(old, new)
        assert not valid
        assert "size" in reason.lower() or "large" in reason.lower()

    def test_accepts_reasonable_update(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## Projects\n- TheOS\n## Notes\n- old fact"
        new = "# Memory\n## Projects\n- TheOS\n- new project\n## Notes\n- updated fact"
        valid, reason = _validate_memory_update(old, new)
        assert valid, f"should accept reasonable update, got: {reason}"

    def test_accepts_when_both_empty(self):
        from src.memory.consolidation import _validate_memory_update

        valid, _ = _validate_memory_update("", "")
        assert valid
```

- [ ] **Step 2: Implementation**

Add `_validate_memory_update(old, new)` function in consolidation.py returning `(valid: bool, reason: str)`.

Wire it in `_persist_consolidation_result` — before writing new memory, validate; if invalid, log warning and skip the memory write (but still write the history entry).

---

### Task 3.2: KG Node Garbage Collection

**Files:**
- Modify: `src/memory/structured.py` or new module
- Modify: `src/memory/rule_cleanup.py` (add GC to cron)

Delete nodes where:
- `superseded_by IS NOT NULL` AND `updated_at < now - 30 days`

Also run `VACUUM` on kg.db after deletion.

- [ ] **Step 1: Tests**

```python
class TestKGGarbageCollection:
    @pytest.mark.asyncio
    async def test_gc_removes_old_superseded_nodes(self, tmp_path):
        from src.memory.structured import StructuredMemoryStore
        from src.memory.kg_gc import gc_superseded_nodes
        from datetime import datetime, timedelta, timezone

        store = StructuredMemoryStore(tmp_path)
        await store.ensure_kg()

        # Add a node, then supersede it with an old timestamp
        # (implementation detail — use KG primitives)
        ...

        deleted = await gc_superseded_nodes(tmp_path, max_age_days=30)
        assert deleted > 0
        await store.close()
```

- [ ] **Step 2: Implementation**

Create `src/memory/kg_gc.py` with:

```python
"""KG node garbage collection — removes long-superseded nodes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from src.memory.structured import StructuredMemoryStore


async def gc_superseded_nodes(workspace: Path, max_age_days: int = 30) -> int:
    """Delete superseded KG nodes older than max_age_days. Returns count deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    store = StructuredMemoryStore(workspace)
    try:
        await store.ensure_kg()
        if store._kg is None:
            return 0

        # Query: find superseded nodes older than cutoff
        db = store._kg._db
        rows = await db.fetchall(
            "SELECT id FROM kg_nodes WHERE superseded_by IS NOT NULL AND updated_at < ?",
            (cutoff,),
        )
        ids = [r["id"] for r in rows]

        for nid in ids:
            await db.execute("DELETE FROM kg_nodes WHERE id = ?", (nid,))
            await db.execute("DELETE FROM kg_edges WHERE from_id = ? OR to_id = ?", (nid, nid))

        # VACUUM to reclaim space
        if ids:
            await db.execute("VACUUM")

        if ids:
            logger.info("KG GC: deleted {} superseded nodes", len(ids))
        return len(ids)
    finally:
        await store.close()
```

Wire into `rule_cleanup.run_structured_rule_cleanup_event`.

---

### Task 3.3: Lint + Integration + Push
