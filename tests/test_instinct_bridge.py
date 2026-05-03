"""Tests for the Instinct-to-KG graduation bridge (_import_kg_pending)."""

from __future__ import annotations

import json
from pathlib import Path

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.knowledge_search import KnowledgeSearch


async def _import_kg_pending(workspace: Path) -> int:
    """Replicate the import logic from MemoryHandler._import_kg_pending for testing."""
    pending_path = workspace / "memory" / "instinct" / "kg_pending.jsonl"
    if not pending_path.exists():
        return 0

    lines = pending_path.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return 0

    db_path = workspace / "memory" / "kg.db"
    kg = KnowledgeGraph(db_path)
    await kg.connect()
    search = KnowledgeSearch(kg)
    await search.ensure_fts()

    imported = 0
    for line in lines:
        record = json.loads(line)
        rule_text = record.get("rule_text", "").strip()
        if not rule_text:
            continue

        domains = record.get("domains", [])
        confidence = record.get("confidence", 0.9)

        await kg.add_node(
            node_type="lesson",
            title=rule_text[:120],
            content=rule_text,
            domains=domains,
            metadata={
                "source": "instinct",
                "confidence": confidence,
                "promoted_at": record.get("promoted_at", ""),
            },
        )
        imported += 1

    if imported:
        await search.rebuild_fts()

    # Truncate after import
    pending_path.write_text("", encoding="utf-8")

    await kg.close()
    return imported


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
