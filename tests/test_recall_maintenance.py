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

        tool = StructuredMemorySearchTool(workspace_resolver=lambda _sk: tmp_path)
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
