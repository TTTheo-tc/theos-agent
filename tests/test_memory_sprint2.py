"""Tests for Memory v2 Sprint 2 — recall schema extension + unified event log."""

from __future__ import annotations

import json

import pytest


class TestRecallSchemaExtension:
    @pytest.mark.asyncio
    async def test_claim_hash_written_when_content_present(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-abc",
                    "content": "always use pytest",
                    "score": 0.9,
                    "domains": [],
                }
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert entry.get("claim_hash")
        assert len(entry["claim_hash"]) == 12

    @pytest.mark.asyncio
    async def test_claim_hash_omitted_when_no_content(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-xyz",
                    "score": 0.5,
                    "domains": [],
                }
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert entry.get("claim_hash") is None or entry.get("claim_hash") == ""

    @pytest.mark.asyncio
    async def test_claim_hash_stable_for_normalized_content(self, tmp_path):
        """Same content with different whitespace/case should hash identically."""
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="q1",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "content": "Always Use Pytest",
                    "score": 0.5,
                    "domains": [],
                }
            ],
        )
        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="q2",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "content": "  always  use   pytest  ",
                    "score": 0.5,
                    "domains": [],
                }
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        lines = [json.loads(line) for line in journal.read_text().strip().split("\n") if line]
        assert lines[0]["claim_hash"] == lines[1]["claim_hash"]

    def test_fold_aggregates_total_score_and_daily_count(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        lines = [
            json.dumps(
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                }
            ),
            json.dumps(
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "query_hash": "h2",
                    "day": "2026-04-14",
                    "score": 0.6,
                }
            ),
            json.dumps(
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "query_hash": "h3",
                    "day": "2026-04-15",
                    "score": 0.7,
                }
            ),
        ]
        journal.write_text("\n".join(lines) + "\n")

        fold_recall_journal(tmp_path)
        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        t = targets["rule-a"]
        assert abs(t["total_score"] - 1.8) < 0.001
        # daily_count tracks the peak single-day count; 2026-04-14 has 2 entries.
        assert t["daily_count"] == 2

    def test_fold_preserves_existing_fields(self, tmp_path):
        """Schema extension must be additive — existing fields still populated."""
        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-b",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.9,
                    "timestamp": "2026-04-14T10:00:00",
                }
            )
            + "\n"
        )

        fold_recall_journal(tmp_path)
        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        t = targets["rule-b"]
        assert t["recall_count"] == 1
        assert t["distinct_query_hashes"] == ["h1"]
        assert t["distinct_days"] == ["2026-04-14"]
        assert t["max_score"] == 0.9
        assert t["last_recalled_at"] == "2026-04-14T10:00:00"


class TestUnifiedEventLog:
    def test_append_memory_event(self, tmp_path):
        from src.memory.memory_events import append_memory_event

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

    def test_append_memory_event_multiple_entries_appended(self, tmp_path):
        from src.memory.memory_events import append_memory_event

        append_memory_event(workspace=tmp_path, event_type="a", payload={"i": 1})
        append_memory_event(workspace=tmp_path, event_type="b", payload={"i": 2})
        events_path = tmp_path / "memory" / "instinct" / "memory_events.jsonl"
        lines = [json.loads(line) for line in events_path.read_text().strip().split("\n") if line]
        assert [e["type"] for e in lines] == ["a", "b"]

    def test_append_memory_event_default_payload(self, tmp_path):
        from src.memory.memory_events import append_memory_event

        append_memory_event(workspace=tmp_path, event_type="x")
        events_path = tmp_path / "memory" / "instinct" / "memory_events.jsonl"
        entry = json.loads(events_path.read_text().strip())
        assert entry["payload"] == {}

    def test_fold_emits_event(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        journal = instinct_dir / "recall_journal.jsonl"
        journal.write_text(
            json.dumps(
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-x",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                }
            )
            + "\n"
        )

        fold_recall_journal(tmp_path)

        events = instinct_dir / "memory_events.jsonl"
        assert events.exists()
        lines = [json.loads(line) for line in events.read_text().strip().split("\n") if line]
        assert any(e["type"] == "memory.recall.folded" for e in lines)

    @pytest.mark.asyncio
    async def test_append_recall_entries_emits_recorded_event(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="pytest",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-abc",
                    "content": "always use pytest",
                    "score": 0.9,
                    "domains": [],
                }
            ],
        )

        events_path = tmp_path / "memory" / "instinct" / "memory_events.jsonl"
        lines = [json.loads(line) for line in events_path.read_text().strip().split("\n") if line]
        recorded = [e for e in lines if e["type"] == "memory.recall.recorded"]
        assert len(recorded) == 1
        assert recorded[0]["payload"]["result_count"] == 1

    @pytest.mark.asyncio
    async def test_ingest_emits_event_even_when_no_rules_updated(self, tmp_path):
        """ingest_recall_to_kg should emit the event regardless of count (count=0 is valid info)."""
        from src.memory.recall_maintenance import ingest_recall_to_kg

        # Seed empty-but-present targets so ingest runs.
        instinct_dir = tmp_path / "memory" / "instinct"
        instinct_dir.mkdir(parents=True)
        (instinct_dir / "recall_targets.json").write_text("{}")

        await ingest_recall_to_kg(tmp_path)

        events_path = instinct_dir / "memory_events.jsonl"
        assert events_path.exists()
        lines = [json.loads(line) for line in events_path.read_text().strip().split("\n") if line]
        assert any(e["type"] == "memory.recall.ingested" for e in lines)

    def test_flush_event_emits_to_unified_log(self, tmp_path):
        from src.agent.loop_memory import MemoryHandler

        MemoryHandler._write_flush_event(tmp_path, "session-xyz", 4)

        events_path = tmp_path / "memory" / "instinct" / "memory_events.jsonl"
        assert events_path.exists()
        lines = [json.loads(line) for line in events_path.read_text().strip().split("\n") if line]
        flushes = [e for e in lines if e["type"] == "memory.flush.completed"]
        assert len(flushes) == 1
        assert flushes[0]["payload"]["session_key"] == "session-xyz"
        assert flushes[0]["payload"]["facts_merged"] == 4


class TestRecallRanking:
    def test_score_components_in_range(self):
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
        result = score_recall_target(target, reference_date="2026-04-14T00:00:00")
        assert 0.0 <= result["score"] <= 1.0
        for comp in (
            "frequency",
            "relevance",
            "diversity",
            "recency",
            "consolidation",
            "conceptual",
        ):
            assert comp in result["components"]
            assert 0.0 <= result["components"][comp] <= 1.0

    def test_rank_applies_count_threshold(self, tmp_path):
        from src.memory.recall_ranking import rank_recall_candidates

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
                "recall_count": 1,
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
        ids = [c["target_id"] for c in candidates]
        assert "rule-weak" not in ids  # filtered by recall_count threshold

    def test_rank_sorted_by_score_desc(self, tmp_path):
        from src.memory.recall_ranking import rank_recall_candidates

        targets = {
            f"rule-{i}": {
                "recall_count": 10,
                "distinct_query_hashes": ["h1", "h2", "h3", "h4", "h5"],
                "distinct_days": ["d1", "d2", "d3", "d4", "d5"],
                "last_recalled_at": "2026-04-14T00:00:00",
                "max_score": 0.5 + i * 0.1,  # increasing
                "total_score": 5.0,
                "daily_count": 2,
            }
            for i in range(3)
        }
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.parent.mkdir(parents=True)
        targets_path.write_text(json.dumps(targets))

        candidates = rank_recall_candidates(tmp_path, min_score=0.0)
        scores = [c["score"] for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_rank_empty_when_no_targets(self, tmp_path):
        from src.memory.recall_ranking import rank_recall_candidates

        assert rank_recall_candidates(tmp_path) == []

    def test_rank_is_rank_only_no_side_effects(self, tmp_path):
        """rank_recall_candidates must not modify KG, ACTIVE.md, or any state."""
        from src.memory.recall_ranking import rank_recall_candidates

        targets = {
            "rule-a": {
                "recall_count": 5,
                "distinct_query_hashes": ["h1", "h2", "h3"],
                "distinct_days": ["d1", "d2"],
                "last_recalled_at": "2026-04-14T00:00:00",
                "max_score": 0.95,
                "total_score": 4.5,
                "daily_count": 2,
            },
        }
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.parent.mkdir(parents=True)
        targets_path.write_text(json.dumps(targets))
        before = targets_path.read_text()

        rank_recall_candidates(tmp_path)

        # No side effects — targets file unchanged
        assert targets_path.read_text() == before
        # No KG files created
        assert not (tmp_path / "memory" / "kg.db").exists()


class TestRuntimeTelemetryPaths:
    @pytest.mark.asyncio
    async def test_structured_memory_search_runtime_writes_claim_hash(self, tmp_path):
        from src.agent.tools.context import ToolContext
        from src.agent.tools.structured_memory import StructuredMemorySearchTool
        from src.memory.structured import StructuredMemoryStore

        store = StructuredMemoryStore(tmp_path)
        try:
            await store.ensure_kg()
            result = await store.record_task(
                session_key="cli:test",
                user_message="记住测试规范",
                response="Always use pytest for integration tests.",
                tools_used=["web_search"],
                routed_skills=["summarize"],
                routing_domains=["coding/testing"],
                selected_primary="coding/testing",
                usage={},
                duration_ms=10.0,
            )
        finally:
            await store.close()

        tool = StructuredMemorySearchTool(
            workspace_resolver=lambda _sk: tmp_path,
            recall_telemetry_enabled=True,
        )
        output = await tool.execute(
            query="pytest integration",
            object_type="rule",
            _context=ToolContext(session_key="cli:test"),
        )
        assert "[rule]" in output

        import asyncio

        await asyncio.sleep(0)

        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        lines = [json.loads(line) for line in journal.read_text().strip().split("\n") if line]
        entry = next(line for line in lines if line["target_id"] == result.rule_ids[0])
        assert entry["claim_hash"]
