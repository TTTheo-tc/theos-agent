"""Tests for the Instinct-to-KG graduation bridge (_import_kg_pending)."""

from __future__ import annotations

import json
from pathlib import Path

from src.agent.loop_memory import MemoryHandler
from src.memory.knowledge_graph import KnowledgeGraph


async def _import_kg_pending(workspace: Path) -> int:
    """Run the production Instinct-to-KG import path."""
    handler = MemoryHandler(
        workspace=workspace,
        memory_config=None,
        orchestrator_config=None,
        group_memory_enabled=False,
        groups_base_dir=workspace / "groups",
    )
    return await handler._import_kg_pending(workspace)


# ---------------------------------------------------------------------------
# TestKGPendingImport
# ---------------------------------------------------------------------------


class TestKGPendingImport:
    async def test_import_lessons_from_jsonl(self, tmp_path):
        pending_dir = tmp_path / "memory" / "instinct"
        pending_dir.mkdir(parents=True)
        pending_path = pending_dir / "kg_pending.jsonl"

        records = [
            {
                "rule_text": "Always run tests before committing",
                "domains": ["coding"],
                "confidence": 0.85,
                "promoted_at": "2026-03-20T00:00:00Z",
            },
            {
                "rule_text": "Use type hints for all function signatures",
                "domains": ["coding", "python"],
                "confidence": 0.9,
            },
        ]
        pending_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        imported = await _import_kg_pending(tmp_path)
        assert imported == 2

        # Verify nodes exist in KG
        kg = KnowledgeGraph(tmp_path / "memory" / "kg.db")
        await kg.connect()
        lessons = await kg.list_nodes(node_type="lesson")
        assert len(lessons) == 2
        titles = {node["title"] for node in lessons}
        assert "Always run tests before committing" in titles
        assert "Use type hints for all function signatures" in titles

        # Verify file was truncated
        assert pending_path.read_text(encoding="utf-8") == ""
        await kg.close()

    async def test_empty_pending_is_noop(self, tmp_path):
        pending_dir = tmp_path / "memory" / "instinct"
        pending_dir.mkdir(parents=True)
        (pending_dir / "kg_pending.jsonl").write_text("", encoding="utf-8")

        imported = await _import_kg_pending(tmp_path)
        assert imported == 0

    async def test_nonexistent_pending_is_noop(self, tmp_path):
        imported = await _import_kg_pending(tmp_path)
        assert imported == 0

    async def test_invalid_pending_records_are_skipped(self, tmp_path):
        pending_dir = tmp_path / "memory" / "instinct"
        pending_dir.mkdir(parents=True)
        pending_path = pending_dir / "kg_pending.jsonl"
        pending_path.write_text(
            "\n".join(
                [
                    "{not-json",
                    json.dumps({"rule_text": ""}),
                    json.dumps({"rule_text": None}),
                    json.dumps({"rule_text": 123}),
                    json.dumps({"rule_text": "Prefer direct production tests", "domains": "bad"}),
                ]
            ),
            encoding="utf-8",
        )

        imported = await _import_kg_pending(tmp_path)
        assert imported == 1

        kg = KnowledgeGraph(tmp_path / "memory" / "kg.db")
        await kg.connect()
        lessons = await kg.list_nodes(node_type="lesson")
        assert len(lessons) == 1
        assert lessons[0]["title"] == "Prefer direct production tests"
        assert lessons[0]["domains"] == ""
        assert pending_path.read_text(encoding="utf-8") == ""
        await kg.close()

    async def test_lesson_linked_to_rule(self, tmp_path):
        """After importing a lesson, it can be linked to a rule via an edge."""
        pending_dir = tmp_path / "memory" / "instinct"
        pending_dir.mkdir(parents=True)
        pending_path = pending_dir / "kg_pending.jsonl"
        pending_path.write_text(
            json.dumps({"rule_text": "Always validate input", "domains": ["security"]}),
            encoding="utf-8",
        )

        await _import_kg_pending(tmp_path)

        # Open KG and add a rule, then link the lesson to it
        kg = KnowledgeGraph(tmp_path / "memory" / "kg.db")
        await kg.connect()

        lessons = await kg.list_nodes(node_type="lesson")
        assert len(lessons) == 1
        lesson_id = lessons[0]["id"]

        rule_id = await kg.add_node(
            node_type="rule",
            title="Input validation is required",
            content="All user inputs must be validated",
        )
        await kg.add_edge(lesson_id, rule_id, "derived_from")

        edges = await kg.find_related(lesson_id)
        assert len(edges) == 1
        assert edges[0]["relation"] == "derived_from"
        assert edges[0]["to_id"] == rule_id
        await kg.close()
