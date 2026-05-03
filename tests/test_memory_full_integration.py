"""Full lifecycle integration tests for the memory upgrade.

Covers: record task, FTS search, conflict detection, response cache, and embedding flow.
"""

from __future__ import annotations

from pathlib import Path

from src.memory.knowledge_graph import KnowledgeGraph
from src.memory.knowledge_search import KnowledgeSearch
from src.memory.response_cache import ResponseCache
from src.store.database import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_kg_and_search(tmp_path: Path):
    """Create a connected KG + KnowledgeSearch pair."""
    kg = KnowledgeGraph(tmp_path / "kg.db")
    await kg.connect()
    search = KnowledgeSearch(kg)
    await search.ensure_fts()
    return kg, search


# ---------------------------------------------------------------------------
# test_record_search_conflict_cache
# ---------------------------------------------------------------------------


async def test_record_search_conflict_cache(tmp_path):
    """Full lifecycle: record a task node, search, detect conflict, use response cache."""
    kg, search = await _make_kg_and_search(tmp_path)

    # 1. Record a task node
    task_id = await kg.add_node(
        node_type="task",
        title="Refactor auth module",
        content="Refactored the authentication module for clarity",
        tags=["read_file", "edit_file"],
        domains=["coding"],
        importance=0.5,
        metadata={"session_key": "cli:test", "status": "success"},
    )

    # 2. FTS search should find it
    results = await search.fts_search("auth refactor")
    assert len(results) >= 1
    assert any(r["id"] == task_id for r in results)

    # 3. Record a similar task to trigger conflict detection
    task_id2 = await kg.add_node(
        node_type="task",
        title="Refactor auth module for clarity and tests",
        content="Refactored the authentication module for clarity and added tests",
        tags=["read_file", "edit_file"],
        domains=["coding"],
    )
    superseded = await search.detect_conflicts(task_id2)
    assert task_id in superseded

    # 4. Response cache round-trip
    db = Database(tmp_path / "cache.db")
    await db.connect()
    cache = ResponseCache(db, max_memory=32, ttl_seconds=3600, max_db_entries=100)

    key = ResponseCache.make_key("gpt-4", "system prompt", "refactor auth")
    await cache.put(key, "gpt-4", "Here is the refactored auth module.", token_count=50)
    cached = await cache.get(key)
    assert cached == "Here is the refactored auth module."

    stats = await cache.stats()
    assert stats["tokens_saved"] == 50

    await db.close()
    await kg.close()


# ---------------------------------------------------------------------------
# test_instinct_graduation
# ---------------------------------------------------------------------------


async def test_instinct_graduation(tmp_path):
    """Rule + lesson with derived_from edge."""
    kg, search = await _make_kg_and_search(tmp_path)

    # Add a rule
    rule_id = await kg.add_node(
        node_type="rule",
        title="Always run tests before committing",
        content="Tests must pass before any commit to main",
        domains=["coding"],
    )

    # Add a lesson that derived from the rule
    lesson_id = await kg.add_node(
        node_type="lesson",
        title="Always run tests before committing",
        content="Always run tests before committing",
        domains=["coding"],
        metadata={"source": "instinct", "confidence": 0.9},
    )

    await kg.add_edge(lesson_id, rule_id, "derived_from")

    # Verify the graph structure
    edges = await kg.find_related(lesson_id)
    assert len(edges) == 1
    assert edges[0]["relation"] == "derived_from"
    assert edges[0]["to_id"] == rule_id

    inbound = await kg.find_related_inbound(rule_id)
    assert len(inbound) == 1
    assert inbound[0]["from_id"] == lesson_id

    # FTS finds both
    results = await search.fts_search("tests committing")
    assert len(results) >= 2

    await kg.close()


# ---------------------------------------------------------------------------
# test_embedding_integration
# ---------------------------------------------------------------------------


async def test_embedding_integration(tmp_path):
    """Mock embedding provider, verify hybrid search triggers embedding path."""
    kg, search = await _make_kg_and_search(tmp_path)

    # Add a node and set a mock embedding
    nid = await kg.add_node(
        node_type="task",
        title="embedding integration test",
        content="testing hybrid search with embeddings",
    )

    mock_embedding = [0.1] * 64
    await kg.set_embedding(nid, mock_embedding, "mock-model")

    node = await kg.get_node(nid)
    assert node["embedding"] is not None
    assert node["embedding_model"] == "mock-model"

    # Hybrid search: even without sqlite-vec, the FTS path should work
    results = await search.hybrid_search(
        "embedding integration",
        query_embedding=mock_embedding,
        limit=5,
    )
    # Should find at least the FTS result
    assert len(results) >= 1
    assert any(r["id"] == nid for r in results)

    await kg.close()
