"""Tests for src/memory/knowledge_search.py — FTS, vector, hybrid, conflict, subgraph."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.knowledge_search import KnowledgeSearch


@pytest.fixture
async def ks(tmp_path):
    kg = KnowledgeGraph(tmp_path / "test.db")
    await kg.connect()
    search = KnowledgeSearch(kg)
    await search.ensure_fts()
    yield kg, search
    await kg.close()


# ---------------------------------------------------------------------------
# TestFTSSearch
# ---------------------------------------------------------------------------


class TestFTSSearch:
    async def test_finds_by_title(self, ks):
        kg, search = ks
        await kg.add_node(
            node_type="task", title="deploy production server", content="step by step"
        )
        results = await search.fts_search("deploy")
        assert len(results) >= 1
        assert "deploy" in results[0]["title"].lower()

    async def test_filters_superseded(self, ks):
        kg, search = ks
        old = await kg.add_node(node_type="task", title="deploy old server")
        new = await kg.add_node(node_type="task", title="deploy new server")
        await kg.supersede(old, new)
        results = await search.fts_search("deploy")
        ids = [r["id"] for r in results]
        assert old not in ids
        assert new in ids

    async def test_no_results(self, ks):
        _, search = ks
        results = await search.fts_search("zzzznonexistent")
        assert results == []

    async def test_filters_by_node_type(self, ks):
        kg, search = ks
        await kg.add_node(node_type="task", title="authentication task")
        await kg.add_node(node_type="rule", title="authentication rule")
        results = await search.fts_search("authentication", node_type="rule")
        assert len(results) == 1
        assert results[0]["node_type"] == "rule"

    async def test_has_scoring_fields(self, ks):
        kg, search = ks
        await kg.add_node(node_type="task", title="scoring test node")
        results = await search.fts_search("scoring")
        assert len(results) >= 1
        assert "final_score" in results[0]
        assert isinstance(results[0]["final_score"], float)


# ---------------------------------------------------------------------------
# TestVectorSearch
# ---------------------------------------------------------------------------


class TestVectorSearch:
    async def test_returns_empty_when_vec_unavailable(self, ks):
        _, search = ks
        # _vec_available is False by default when sqlite-vec is not installed
        results = await search._vector_search([0.1, 0.2, 0.3])
        assert results == []

    async def test_actual_vector_search(self, ks):
        kg, search = ks
        if not search._vec_available:
            pytest.skip("sqlite-vec not installed")
        nid = await kg.add_node(node_type="task", title="vector test")
        vec = [0.1] * 128
        await kg.set_embedding(nid, vec, "test")
        results = await search._vector_search(vec, limit=5)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# TestHybridSearch
# ---------------------------------------------------------------------------


class TestHybridSearch:
    async def test_degrades_to_fts_without_embedding(self, ks):
        kg, search = ks
        await kg.add_node(node_type="task", title="hybrid fallback test")
        results = await search.hybrid_search("hybrid fallback", query_embedding=[], limit=5)
        assert len(results) >= 1

    async def test_hybrid_merges_fts_and_vector(self, ks):
        kg, search = ks
        if not search._vec_available:
            pytest.skip("sqlite-vec not installed")
        nid = await kg.add_node(node_type="task", title="merge test content")
        vec = [0.5] * 128
        await kg.set_embedding(nid, vec, "test")
        results = await search.hybrid_search("merge test", query_embedding=vec, limit=5)
        assert len(results) >= 1

    async def test_no_query_embedding_fallback(self, ks):
        kg, search = ks
        await kg.add_node(node_type="task", title="fallback embedding test")
        # Empty embedding list — should degrade to FTS
        results = await search.hybrid_search("fallback embedding", query_embedding=[], limit=5)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# TestConflict
# ---------------------------------------------------------------------------


class TestConflict:
    async def test_jaccard_supersession(self, ks):
        kg, search = ks
        old = await kg.add_node(
            node_type="task",
            title="deploy production server step by step",
            content="deploy production server step by step guide",
        )
        new = await kg.add_node(
            node_type="task",
            title="deploy production server step by step guide updated",
            content="deploy production server step by step guide updated",
        )
        superseded = await search.detect_conflicts(new)
        assert old in superseded

    async def test_different_nodes_not_superseded(self, ks):
        kg, search = ks
        await kg.add_node(node_type="task", title="deploy server", content="deploy instructions")
        new = await kg.add_node(
            node_type="task",
            title="configure monitoring alerts",
            content="setup prometheus and grafana alerting",
        )
        superseded = await search.detect_conflicts(new)
        assert superseded == []

    async def test_antonym_creates_conflicts_with_edge(self, ks):
        kg, search = ks
        old = await kg.add_node(
            node_type="rule",
            title="always use strict mode in JavaScript code for web apps",
            content="always use strict mode in JavaScript code for web apps and safety",
        )
        new = await kg.add_node(
            node_type="rule",
            title="never use strict mode in JavaScript code for web apps",
            content="never use strict mode in JavaScript code for web apps and compatibility",
        )
        superseded = await search.detect_conflicts(new)
        # Antonym pair detected — should create conflicts_with edge, not supersede
        assert old not in superseded
        edges = await kg.find_related(new)
        relations = [e["relation"] for e in edges]
        assert "conflicts_with" in relations

    async def test_vector_similarity_supersedes(self, ks):
        kg, search = ks
        old = await kg.add_node(node_type="task", title="old task")
        new = await kg.add_node(node_type="task", title="new task")
        await kg.set_embedding(new, [0.5] * 4, "test")
        search._vec_available = True
        search._vector_search = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": old, "vec_score": 0.91, "superseded_by": None}]
        )

        superseded = await search.detect_conflicts(new)

        assert superseded == [old]
        old_node = await kg.get_node(old)
        assert old_node["superseded_by"] == new

    async def test_vector_similarity_ignores_low_vec_score(self, ks):
        kg, search = ks
        old = await kg.add_node(
            node_type="task",
            title="deploy production server step by step",
            content="deploy production server step by step guide",
        )
        new = await kg.add_node(
            node_type="task",
            title="deploy production server step by step guide updated",
            content="deploy production server step by step guide updated",
        )
        await kg.set_embedding(new, [0.5] * 4, "test")
        search._vec_available = True
        search._vector_search = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": old, "vec_score": 0.2, "superseded_by": None}]
        )

        superseded = await search.detect_conflicts(new)

        assert superseded == []
        old_node = await kg.get_node(old)
        assert old_node["superseded_by"] is None


# ---------------------------------------------------------------------------
# TestSubgraph
# ---------------------------------------------------------------------------


class TestSubgraph:
    async def test_single_node(self, ks):
        kg, search = ks
        nid = await kg.add_node(node_type="task", title="single")
        sg = await search.get_subgraph(nid)
        assert len(sg["nodes"]) == 1
        assert sg["edges"] == []

    async def test_chain_traversal(self, ks):
        kg, search = ks
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="task", title="b")
        c = await kg.add_node(node_type="task", title="c")
        await kg.add_edge(a, b, "next")
        await kg.add_edge(b, c, "next")
        sg = await search.get_subgraph(a, max_depth=3)
        node_ids = {n["id"] for n in sg["nodes"]}
        assert a in node_ids
        assert b in node_ids
        assert c in node_ids

    async def test_depth_limit(self, ks):
        kg, search = ks
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="task", title="b")
        c = await kg.add_node(node_type="task", title="c")
        await kg.add_edge(a, b, "next")
        await kg.add_edge(b, c, "next")
        sg = await search.get_subgraph(a, max_depth=1)
        node_ids = {n["id"] for n in sg["nodes"]}
        assert a in node_ids
        assert b in node_ids
        # c is at depth 2 — should not be included
        assert c not in node_ids

    async def test_nonexistent_root(self, ks):
        _, search = ks
        sg = await search.get_subgraph("nonexistent-id")
        assert sg["nodes"] == []
        assert sg["edges"] == []

    async def test_bidirectional(self, ks):
        kg, search = ks
        a = await kg.add_node(node_type="task", title="a")
        b = await kg.add_node(node_type="task", title="b")
        c = await kg.add_node(node_type="task", title="c")
        await kg.add_edge(a, b, "next")
        await kg.add_edge(c, b, "ref")
        # Start from b — should find both a (inbound) and nothing outbound,
        # but also c via inbound edge
        sg = await search.get_subgraph(b, max_depth=2)
        node_ids = {n["id"] for n in sg["nodes"]}
        assert a in node_ids
        assert b in node_ids
        assert c in node_ids
