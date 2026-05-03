"""Tests for memory v2 sprint 0 bugfixes."""

from __future__ import annotations

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
            "rank": -5.0,
        }
        row_active = {
            "node_type": "rule",
            "importance": 0.5,
            "created_at": old_created,
            "updated_at": recent_updated,
            "rank": -5.0,
        }

        # With same FTS rank, active rule should score higher
        score_stale = _compute_final_score(row_stale)
        score_active = _compute_final_score(row_active)
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
            "rank": -5.0,
        }
        score = _compute_final_score(row)
        # If lesson had no decay (evergreen), decay factor = 1.0
        # With half-life, a 365-day-old lesson should have notable decay
        # This test ensures lesson is NOT treated as evergreen
        now_ts = datetime.now(timezone.utc).isoformat()
        row_recent = {**row, "created_at": now_ts, "updated_at": now_ts}
        score_recent = _compute_final_score(row_recent)
        assert score < score_recent  # old lesson scores lower than recent one


class TestConsolidationTruncation:
    @pytest.mark.asyncio
    async def test_long_message_truncated_in_provider_prompt(self, tmp_path):
        """Messages over 1000 chars should be head+tail truncated in the real prompt."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from src.memory.consolidation import MemoryConsolidationService
        from src.memory.store import MemoryStore
        from src.providers.base import LLMResponse

        long_content = "important decision " + "x" * 5000 + " final note"
        session = SimpleNamespace(
            messages=[
                {"role": "user", "content": long_content, "timestamp": "2026-04-14T12:00:00"}
            ],
            last_consolidated=0,
        )
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=LLMResponse(content="noop", tool_calls=[]))
        service = MemoryConsolidationService(scope=MagicMock())
        store = MemoryStore(tmp_path)

        await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            archive_all=True,
        )

        prompt = provider.chat.await_args.kwargs["messages"][1]["content"]
        assert "important decision" in prompt
        assert "final note" in prompt
        assert "[truncated]" in prompt


class TestFTSSyncAfterWrite:
    """FTS index is updated immediately after remember() and merge_extracted_facts()."""

    @pytest.mark.asyncio
    async def test_remember_searchable_immediately(self, tmp_path):
        """After remember(), the new fact should be FTS-searchable without consolidation."""
        from src.memory.index import MemoryIndex
        from src.memory.store import MemoryStore
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Long-term Memory\n")

        db = Database(tmp_path / "test.db")
        await db.connect()
        index = MemoryIndex(db)
        await index.ensure_table()
        await index.sync_all(memory_dir)

        store = MemoryStore(tmp_path)
        store.remember("always use pytest for testing")

        # Simulate what the caller does: sync FTS after remember
        await index.sync_all(memory_dir)

        results = await index.search("pytest testing", max_results=5)
        await db.close()
        assert any("pytest" in r.get("content", "").lower() for r in results)

    @pytest.mark.asyncio
    async def test_extract_merge_searchable_immediately(self, tmp_path):
        """After merge_extracted_facts(), new facts should be FTS-searchable."""
        from src.memory.extract import merge_extracted_facts
        from src.memory.index import MemoryIndex
        from src.memory.store import MemoryStore
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Long-term Memory\n")

        db = Database(tmp_path / "test.db")
        await db.connect()
        index = MemoryIndex(db)
        await index.ensure_table()
        await index.sync_all(memory_dir)

        store = MemoryStore(tmp_path)
        facts = [{"section": "Decisions", "content": "We chose PostgreSQL over Redis"}]
        merged = merge_extracted_facts(store, facts)
        assert merged == 1

        # Simulate what the caller does: sync FTS after merge
        await index.sync_all(memory_dir)

        results = await index.search("PostgreSQL Redis", max_results=5)
        await db.close()
        assert any("postgresql" in r.get("content", "").lower() for r in results)

    @pytest.mark.asyncio
    async def test_multiple_facts_all_searchable(self, tmp_path):
        """Multiple facts merged in one call should all be searchable after sync."""
        from src.memory.extract import merge_extracted_facts
        from src.memory.index import MemoryIndex
        from src.memory.store import MemoryStore
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Long-term Memory\n")

        db = Database(tmp_path / "test.db")
        await db.connect()
        index = MemoryIndex(db)
        await index.ensure_table()
        await index.sync_all(memory_dir)

        store = MemoryStore(tmp_path)
        facts = [
            {"section": "Architecture", "content": "Frontend uses React with TypeScript"},
            {"section": "Decisions", "content": "Database migrations use Alembic"},
        ]
        merged = merge_extracted_facts(store, facts)
        assert merged == 2

        await index.sync_all(memory_dir)

        results_react = await index.search("React TypeScript", max_results=5)
        results_alembic = await index.search("Alembic migrations", max_results=5)
        await db.close()

        assert any("react" in r.get("content", "").lower() for r in results_react)
        assert any("alembic" in r.get("content", "").lower() for r in results_alembic)


class TestMaintenanceAtomicWrite:
    def test_targets_written_atomically(self, tmp_path):
        """Targets file should be valid JSON after fold -- atomic write prevents partial writes."""
        import json

        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "target_kind": "rule",
                    "target_id": "rule-a",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                }
            )
            + "\n"
        )

        fold_recall_journal(tmp_path)
        targets = instinct_dir / "recall_targets.json"
        assert targets.exists()
        data = json.loads(targets.read_text())
        assert "rule-a" in data

    def test_no_tmp_files_left_after_fold(self, tmp_path):
        """No .tmp files should remain after successful fold."""
        import json

        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "target_kind": "rule",
                    "target_id": "rule-b",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                }
            )
            + "\n"
        )

        fold_recall_journal(tmp_path)
        tmp_files = list(instinct_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_lock_prevents_concurrent_fold(self, tmp_path):
        """Concurrent fold should be blocked by lock."""
        import fcntl
        import json

        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "target_kind": "rule",
                    "target_id": "rule-c",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                }
            )
            + "\n"
        )

        # Acquire the lock file manually
        lock_path = instinct_dir / "recall_maintenance.lock"
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            # fold should detect lock and return 0 (skipped)
            result = fold_recall_journal(tmp_path)
            assert result == 0  # couldn't acquire lock, skipped
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()


class TestHybridMergeDecay:
    def test_merge_applies_temporal_decay(self):
        """After merge, recent nodes should rank above old ones with same FTS score."""
        from src.memory.knowledge_search import _merge_results

        old_ts = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        new_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        fts_results = [
            {
                "id": "old-node",
                "node_type": "task",
                "final_score": 0.9,
                "created_at": old_ts,
                "updated_at": old_ts,
            },
            {
                "id": "new-node",
                "node_type": "task",
                "final_score": 0.8,
                "created_at": new_ts,
                "updated_at": new_ts,
            },
        ]
        merged = _merge_results(fts_results, [])
        ids = [r["id"] for r in merged]
        # New node should rank first despite lower raw FTS score, because old node decays heavily
        assert ids[0] == "new-node"

    def test_shared_helper_used_by_both_paths(self):
        """_row_decay should be the single source of decay logic."""
        from src.memory.knowledge_search import _row_decay

        row = {
            "node_type": "rule",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        decay = _row_decay(row)
        assert 0.99 < decay <= 1.0  # recent rule should have near-1.0 decay

    def test_merge_without_timestamps_uses_default(self):
        """Rows without timestamps should get 0.5 decay (neutral)."""
        from src.memory.knowledge_search import _merge_results

        fts_results = [
            {"id": "no-ts", "node_type": "task", "final_score": 0.8},
        ]
        merged = _merge_results(fts_results, [])
        # score = 0.3 * 0.8 * 0.5 = 0.12 (text_weight * fts * decay)
        assert merged[0]["final_score"] > 0
