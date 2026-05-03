"""Sprint 3: Safety + Bridge tests."""

from __future__ import annotations

import pytest


class TestConsolidationValidation:
    def test_rejects_output_dropping_pinned_section(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## Projects\n<!-- pinned -->\n- TheOS\n\n## Notes\n- misc\n"
        new = "# Memory\n## Notes\n- misc\n"  # dropped pinned Projects!
        valid, reason = _validate_memory_update(old, new)
        assert not valid
        assert "pinned" in reason.lower()

    def test_rejects_massive_section_drop(self):
        from src.memory.consolidation import _validate_memory_update

        old = "\n".join([f"## Section{i}\n- content\n" for i in range(10)])
        new = "## Section1\n- content\n"  # dropped 9/10
        valid, reason = _validate_memory_update(old, new)
        assert not valid

    def test_rejects_explosive_size(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## One\n- x\n"
        new = "# Memory\n" + "## Section\n- xxxxxx\n" * 2000  # explosion
        valid, reason = _validate_memory_update(old, new)
        assert not valid

    def test_accepts_reasonable_update(self):
        from src.memory.consolidation import _validate_memory_update

        old = "# Memory\n## Projects\n- TheOS\n\n## Notes\n- old fact\n"
        new = "# Memory\n## Projects\n- TheOS\n- new project\n\n## Notes\n- updated fact\n"
        valid, reason = _validate_memory_update(old, new)
        assert valid, f"should accept reasonable update, got: {reason}"

    def test_accepts_empty_to_empty(self):
        from src.memory.consolidation import _validate_memory_update

        valid, _ = _validate_memory_update("", "")
        assert valid

    def test_accepts_empty_to_first_section(self):
        from src.memory.consolidation import _validate_memory_update

        # First real update — previously empty memory
        valid, _ = _validate_memory_update("", "# Memory\n## Notes\n- first\n")
        assert valid


class TestKGGarbageCollection:
    @pytest.mark.asyncio
    async def test_gc_no_nodes_returns_zero(self, tmp_path):
        from src.memory.kg_gc import gc_superseded_nodes

        count = await gc_superseded_nodes(tmp_path, max_age_days=30)
        assert count == 0

    @pytest.mark.asyncio
    async def test_gc_preserves_active_nodes(self, tmp_path):
        """Non-superseded nodes must never be deleted."""
        from src.memory.kg_gc import gc_superseded_nodes
        from src.memory.structured import StructuredMemoryStore

        store = StructuredMemoryStore(tmp_path)
        await store.ensure_kg()
        # Add an active node (not superseded)
        assert store._kg is not None
        await store._kg.add_node(
            node_type="rule",
            title="active rule",
            content="keep me",
            domains=[],
            metadata={},
            importance=0.5,
        )
        await store.close()

        # GC should not touch active nodes even with max_age_days=0
        deleted = await gc_superseded_nodes(tmp_path, max_age_days=0)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_gc_deletes_old_superseded(self, tmp_path):
        """Superseded nodes older than cutoff should be deleted."""
        from datetime import datetime, timedelta, timezone

        from src.memory.kg_gc import gc_superseded_nodes
        from src.memory.structured import StructuredMemoryStore

        store = StructuredMemoryStore(tmp_path)
        await store.ensure_kg()
        assert store._kg is not None

        # Add node A (old) and B (new)
        await store._kg.add_node(
            node_type="rule",
            title="old",
            content="x",
            domains=[],
            metadata={},
            importance=0.5,
        )
        await store._kg.add_node(
            node_type="rule",
            title="new",
            content="y",
            domains=[],
            metadata={},
            importance=0.5,
        )
        # Find the node IDs
        all_rows = await store._kg._db.fetchall("SELECT id, title FROM kg_nodes")
        old_id = next(r["id"] for r in all_rows if r["title"] == "old")
        new_id = next(r["id"] for r in all_rows if r["title"] == "new")

        # Backdate old node and mark as superseded
        past = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        await store._kg._db.execute(
            "UPDATE kg_nodes SET superseded_by = ?, updated_at = ? WHERE id = ?",
            (new_id, past, old_id),
        )
        await store.close()

        deleted = await gc_superseded_nodes(tmp_path, max_age_days=30)
        assert deleted == 1
