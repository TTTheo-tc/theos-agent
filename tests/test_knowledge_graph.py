"""Tests for src/memory/knowledge_graph.py — node/edge CRUD, scoring, embeddings."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta, timezone

import pytest

from src.memory.knowledge_graph import KnowledgeGraph, compute_importance, temporal_decay


@pytest.fixture
async def kg(tmp_path):
    g = KnowledgeGraph(tmp_path / "test.db")
    await g.connect()
    yield g
    await g.close()


# ---------------------------------------------------------------------------
# TestNodeCRUD
# ---------------------------------------------------------------------------


class TestNodeCRUD:
    async def test_add_node(self, kg):
        nid = await kg.add_node(node_type="task", title="hello")
        assert nid.startswith("task-")

    async def test_get_node(self, kg):
        nid = await kg.add_node(node_type="task", title="hello", content="world")
        node = await kg.get_node(nid)
        assert node is not None
        assert node["title"] == "hello"
        assert node["content"] == "world"

    async def test_get_missing_returns_none(self, kg):
        assert await kg.get_node("nonexistent-id") is None

    async def test_update_node(self, kg):
        nid = await kg.add_node(node_type="rule", title="old title")
        await kg.update_node(nid, title="new title")
        node = await kg.get_node(nid)
        assert node["title"] == "new title"

    async def test_update_node_merges_metadata(self, kg):
        nid = await kg.add_node(
            node_type="rule",
            title="rule",
            metadata={"source_task_ids": ["task-1"], "confidence": 0.6},
        )

        await kg.update_node(nid, metadata={"confidence": 0.7, "last_seen_at": "now"})

        node = await kg.get_node(nid)
        meta = json.loads(node["metadata"])
        assert meta == {
            "source_task_ids": ["task-1"],
            "confidence": 0.7,
            "last_seen_at": "now",
        }

    async def test_update_node_tolerates_invalid_existing_metadata(self, kg):
        nid = await kg.add_node(node_type="rule", title="rule", metadata={"old": True})
        await kg._db.execute(
            "UPDATE kg_nodes SET metadata = ? WHERE id = ?",
            ("{bad json", nid),
        )

        await kg.update_node(nid, metadata={"fixed": True})

        node = await kg.get_node(nid)
        assert json.loads(node["metadata"]) == {"fixed": True}

    async def test_update_node_rejects_invalid_columns(self, kg):
        nid = await kg.add_node(node_type="rule", title="rule")

        with pytest.raises(ValueError, match="Invalid column names"):
            await kg.update_node(nid, nope="bad")

    async def test_supersede(self, kg):
        old = await kg.add_node(node_type="task", title="old")
        new = await kg.add_node(node_type="task", title="new")
        await kg.supersede(old, new)
        node = await kg.get_node(old)
        assert node["superseded_by"] == new

    async def test_count(self, kg):
        assert await kg.count() == 0
        await kg.add_node(node_type="task", title="a")
        await kg.add_node(node_type="rule", title="b")
        assert await kg.count() == 2

    async def test_count_by_type(self, kg):
        """count() returns total; list_nodes filters by type."""
        await kg.add_node(node_type="task", title="t1")
        await kg.add_node(node_type="task", title="t2")
        await kg.add_node(node_type="rule", title="r1")
        tasks = await kg.list_nodes(node_type="task")
        rules = await kg.list_nodes(node_type="rule")
        assert len(tasks) == 2
        assert len(rules) == 1

    async def test_list_nodes(self, kg):
        await kg.add_node(node_type="task", title="a")
        await kg.add_node(node_type="task", title="b")
        nodes = await kg.list_nodes(node_type="task")
        assert len(nodes) == 2
        # newest first
        assert nodes[0]["title"] == "b"

    async def test_list_nodes_excludes_superseded(self, kg):
        old = await kg.add_node(node_type="task", title="old")
        new = await kg.add_node(node_type="task", title="new")
        await kg.supersede(old, new)
        nodes = await kg.list_nodes(node_type="task", exclude_superseded=True)
        assert len(nodes) == 1
        assert nodes[0]["id"] == new

    async def test_list_nodes_includes_superseded(self, kg):
        old = await kg.add_node(node_type="task", title="old")
        new = await kg.add_node(node_type="task", title="new")
        await kg.supersede(old, new)
        nodes = await kg.list_nodes(node_type="task", exclude_superseded=False)
        assert len(nodes) == 2


# ---------------------------------------------------------------------------
# TestEdges
# ---------------------------------------------------------------------------


class TestEdges:
    async def test_add_edge(self, kg):
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="rule", title="b")
        await kg.add_edge(a, b, "derived")
        related = await kg.find_related(a)
        assert len(related) == 1
        assert related[0]["to_id"] == b
        assert related[0]["relation"] == "derived"

    async def test_find_related(self, kg):
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="rule", title="b")
        c = await kg.add_node(node_type="rule", title="c")
        await kg.add_edge(a, b, "derived")
        await kg.add_edge(a, c, "derived")
        related = await kg.find_related(a)
        assert len(related) == 2

    async def test_find_related_inbound(self, kg):
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="rule", title="b")
        await kg.add_edge(a, b, "derived")
        inbound = await kg.find_related_inbound(b)
        assert len(inbound) == 1
        assert inbound[0]["from_id"] == a

    async def test_duplicate_edge_ignored(self, kg):
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="rule", title="b")
        await kg.add_edge(a, b, "derived")
        await kg.add_edge(a, b, "derived")  # duplicate — should not raise
        related = await kg.find_related(a)
        assert len(related) == 1


# ---------------------------------------------------------------------------
# TestScoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_compute_importance_keyword_boost(self):
        base = compute_importance("task", "do something")
        boosted = compute_importance("task", "you MUST always do this critical thing")
        assert boosted > base

    def test_temporal_decay_recent(self):
        recent = datetime.now(timezone.utc).isoformat()
        d = temporal_decay(recent, half_life_days=30.0)
        assert d > 0.99  # very recent, nearly 1.0

    def test_temporal_decay_old(self):
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        d = temporal_decay(old, half_life_days=30.0)
        assert d < 0.1

    def test_temporal_decay_evergreen(self):
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        d = temporal_decay(old, half_life_days=0)
        assert d == 1.0


# ---------------------------------------------------------------------------
# TestEmbedding
# ---------------------------------------------------------------------------


class TestEmbedding:
    async def test_set_embedding_and_get_node_has_blob(self, kg):
        nid = await kg.add_node(node_type="task", title="embed me")
        vec = [0.1, 0.2, 0.3]
        await kg.set_embedding(nid, vec, "test-model")
        node = await kg.get_node(nid)
        assert node["embedding"] is not None
        assert node["embedding_model"] == "test-model"
        # Verify the blob encodes correctly
        unpacked = list(struct.unpack(f"{len(vec)}f", node["embedding"]))
        assert len(unpacked) == 3
        assert abs(unpacked[0] - 0.1) < 1e-5

    async def test_list_nodes_missing_embedding(self, kg):
        """Nodes without embedding can be found by checking embedding IS NULL."""
        await kg.add_node(node_type="task", title="no-emb")
        nid2 = await kg.add_node(node_type="task", title="has-emb")
        await kg.set_embedding(nid2, [1.0, 2.0], "m")

        # Query directly for nodes missing embeddings
        rows = await kg._db.fetchall(
            "SELECT id FROM kg_nodes WHERE embedding IS NULL AND node_type = ?",
            ("task",),
        )
        missing_ids = [dict(r)["id"] for r in rows]
        assert len(missing_ids) == 1
        assert "no-emb" in (await kg.get_node(missing_ids[0]))["title"]
