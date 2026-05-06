"""Tests for recall maintenance — journal fold + KG ingestion."""

from __future__ import annotations

import asyncio
import json

import pytest


def _write_journal(tmp_path, entries):
    journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    journal.write_text("\n".join(lines) + "\n")
    return journal


class TestFoldRecallJournal:
    def test_fold_creates_targets(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-abc",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.9,
                },
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-abc",
                    "query_hash": "h2",
                    "day": "2026-04-14",
                    "score": 0.8,
                },
                {
                    "target_kind": "markdown_section",
                    "target_id": None,
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.7,
                },
            ],
        )
        result = fold_recall_journal(tmp_path)
        assert result == 1  # only 1 KG target folded

        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        assert targets_path.exists()
        targets = json.loads(targets_path.read_text())
        assert "rule-abc" in targets
        assert targets["rule-abc"]["recall_count"] == 2
        assert len(targets["rule-abc"]["distinct_query_hashes"]) == 2
        assert targets["rule-abc"]["max_score"] == 0.9

    def test_fold_incremental_with_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-a",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.9,
                },
            ],
        )
        fold_recall_journal(tmp_path)

        # Append more
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        with open(journal, "a") as f:
            f.write(
                json.dumps(
                    {
                        "target_kind": "kg_rule",
                        "target_id": "rule-a",
                        "query_hash": "h2",
                        "day": "2026-04-15",
                        "score": 0.85,
                    }
                )
                + "\n"
            )

        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-a"]["recall_count"] == 2
        assert len(targets["rule-a"]["distinct_days"]) == 2

    def test_fold_rebuilds_from_scratch_if_no_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-x",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                },
            ],
        )
        # Write targets but delete checkpoint — should rebuild
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        targets_path.write_text("{}")

        fold_recall_journal(tmp_path)
        targets = json.loads(targets_path.read_text())
        assert targets["rule-x"]["recall_count"] == 1

    def test_fold_discards_stale_targets_if_checkpoint_missing(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-fresh",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.5,
                },
            ],
        )
        targets_path = tmp_path / "memory" / "instinct" / "recall_targets.json"
        targets_path.write_text(json.dumps({"rule-stale": {"recall_count": 99}}))

        fold_recall_journal(tmp_path)

        targets = json.loads(targets_path.read_text())
        assert "rule-stale" not in targets
        assert targets["rule-fresh"]["recall_count"] == 1

    def test_fold_recovers_from_corrupt_checkpoint(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-y",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        # Write corrupt checkpoint
        cp = tmp_path / "memory" / "instinct" / "recall_targets.checkpoint.json"
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("NOT VALID JSON{{{")

        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-y"]["recall_count"] == 1

    def test_fold_discards_stale_targets_when_checkpoint_corrupt(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-fresh-corrupt",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text(
            json.dumps({"rule-stale": {"recall_count": 99}})
        )
        (instinct_dir / "recall_targets.checkpoint.json").write_text("NOT VALID JSON{{{")

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert "rule-stale" not in targets
        assert targets["rule-fresh-corrupt"]["recall_count"] == 1

    def test_fold_recovers_from_non_numeric_checkpoint_offset(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-bad-offset",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text(
            json.dumps({"rule-stale": {"recall_count": 10}})
        )
        (instinct_dir / "recall_targets.checkpoint.json").write_text(
            json.dumps({"byte_offset": "not-an-int"})
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert "rule-stale" not in targets
        assert targets["rule-bad-offset"]["recall_count"] == 1

    def test_fold_discards_stale_targets_when_checkpoint_offset_missing(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-missing-offset",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text(
            json.dumps({"rule-stale": {"recall_count": 99}})
        )
        (instinct_dir / "recall_targets.checkpoint.json").write_text(json.dumps({}))

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert "rule-stale" not in targets
        assert targets["rule-missing-offset"]["recall_count"] == 1

    def test_fold_discards_stale_targets_when_checkpoint_offset_negative(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-negative-offset",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text(
            json.dumps({"rule-stale": {"recall_count": 99}})
        )
        (instinct_dir / "recall_targets.checkpoint.json").write_text(
            json.dumps({"byte_offset": -1})
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert "rule-stale" not in targets
        assert targets["rule-negative-offset"]["recall_count"] == 1

    @pytest.mark.parametrize("offset", [1.5, True])
    def test_fold_discards_stale_targets_when_checkpoint_offset_not_int(self, tmp_path, offset):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-non-int-offset",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.6,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text(
            json.dumps({"rule-stale": {"recall_count": 99}})
        )
        (instinct_dir / "recall_targets.checkpoint.json").write_text(
            json.dumps({"byte_offset": offset})
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert "rule-stale" not in targets
        assert targets["rule-non-int-offset"]["recall_count"] == 1

    def test_fold_recovers_from_missing_targets(self, tmp_path):
        """recall_targets.json deleted but checkpoint exists — rebuild from journal."""
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-z",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.7,
                },
            ],
        )
        # First fold creates both files
        fold_recall_journal(tmp_path)
        # Delete targets but keep checkpoint
        (tmp_path / "memory" / "instinct" / "recall_targets.json").unlink()
        # Append more to journal
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        with open(journal, "a") as f:
            f.write(
                json.dumps(
                    {
                        "target_kind": "kg_rule",
                        "target_id": "rule-z",
                        "query_hash": "h2",
                        "day": "2026-04-15",
                        "score": 0.8,
                    }
                )
                + "\n"
            )
        # Fold should detect targets missing and rebuild
        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert targets["rule-z"]["recall_count"] >= 1

    def test_fold_rebuilds_when_targets_invalid_but_checkpoint_valid(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        journal = _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-invalid-targets",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": 0.7,
                },
            ],
        )
        instinct_dir = tmp_path / "memory" / "instinct"
        (instinct_dir / "recall_targets.json").write_text("[]")
        (instinct_dir / "recall_targets.checkpoint.json").write_text(
            json.dumps({"byte_offset": journal.stat().st_size})
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((instinct_dir / "recall_targets.json").read_text())
        assert targets["rule-invalid-targets"]["recall_count"] == 1

    def test_fold_caps_hashes_and_days(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        entries = [
            {
                "target_kind": "kg_rule",
                "target_id": "rule-big",
                "query_hash": f"h{i}",
                "day": f"2026-04-{i:02d}",
                "score": 0.5,
            }
            for i in range(1, 50)
        ]
        _write_journal(tmp_path, entries)
        fold_recall_journal(tmp_path)
        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert len(targets["rule-big"]["distinct_query_hashes"]) <= 32
        assert len(targets["rule-big"]["distinct_days"]) <= 16

    def test_fold_ignores_non_numeric_scores(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-score",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": "high",
                },
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-score",
                    "query_hash": "h2",
                    "day": "2026-04-15",
                    "score": 0.7,
                },
            ],
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        target = targets["rule-score"]
        assert target["recall_count"] == 2
        assert target["max_score"] == 0.7
        assert target["total_score"] == 0.7

    def test_fold_ignores_non_finite_scores(self, tmp_path):
        from src.memory.recall_maintenance import fold_recall_journal

        _write_journal(
            tmp_path,
            [
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-finite",
                    "query_hash": "h1",
                    "day": "2026-04-14",
                    "score": "nan",
                },
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-finite",
                    "query_hash": "h2",
                    "day": "2026-04-15",
                    "score": "inf",
                },
            ],
        )

        fold_recall_journal(tmp_path)

        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        target = targets["rule-finite"]
        assert target["recall_count"] == 2
        assert target["max_score"] == 0.0
        assert target["total_score"] == 0.0

    @pytest.mark.asyncio
    async def test_fold_accepts_real_structured_search_rule_entries(self, tmp_path):
        from src.agent.tools.context import ToolContext
        from src.agent.tools.structured_memory import StructuredMemorySearchTool
        from src.memory.recall_maintenance import fold_recall_journal
        from src.memory.structured import StructuredMemoryStore

        store = StructuredMemoryStore(tmp_path)
        try:
            await store.ensure_kg()
            result = await store.record_task(
                session_key="cli:test",
                user_message="帮我分析量化策略回测",
                response="建议先做回测，再控制风险。",
                tools_used=["web_search"],
                routed_skills=["summarize"],
                routing_domains=["finance/general"],
                selected_primary="finance/general",
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
            query="finance",
            object_type="rule",
            _context=ToolContext(session_key="cli:test"),
        )
        assert "[rule]" in output

        # Telemetry is scheduled with create_task(); yield once so the journal write runs.
        await asyncio.sleep(0)

        folded = fold_recall_journal(tmp_path)
        assert folded == 1

        targets = json.loads((tmp_path / "memory" / "instinct" / "recall_targets.json").read_text())
        assert result.rule_ids[0] in targets
        assert targets[result.rule_ids[0]]["recall_count"] == 1
