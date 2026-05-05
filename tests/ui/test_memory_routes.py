from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.ui.server import create_ui_app


@pytest.fixture
async def workspace(tmp_path: Path):
    """Create a workspace with KG data."""
    from src.memory.knowledge_graph import KnowledgeGraph

    kg_dir = tmp_path / "memory"
    kg_dir.mkdir()
    kg = KnowledgeGraph(kg_dir / "kg.db")
    await kg.connect()
    await kg.add_node(
        node_type="rule",
        title="Test Rule",
        content="Always test first",
        importance=0.8,
        tags=["testing"],
    )
    await kg.close()

    (kg_dir / "MEMORY.md").write_text(
        "## Section One\nContent here\n\n## Section Two\nMore content\n"
    )

    instinct = kg_dir / "instinct"
    rules = instinct / "rules"
    rules.mkdir(parents=True)
    (rules / "ACTIVE.md").write_text(
        "# Active Rules\n\n"
        "- [rule-a] Always test memory UI changes.  <!-- scope:domain_boost domains:coding/general boost:1 class:adaptive conf:0.8 -->\n",
        encoding="utf-8",
    )
    (rules / "CANDIDATES.md").write_text(
        "# Candidate Rules\n\n### 2026-05-05 — session\nconfidence: 0.75 | demand: feature\n\n"
        "- When surfacing memory, show recall signals next to rules.\n",
        encoding="utf-8",
    )
    (instinct / "recall_targets.json").write_text(
        json.dumps(
            {
                "rule-a": {
                    "recall_count": 4,
                    "distinct_query_hashes": ["q1", "q2"],
                    "distinct_days": ["2026-05-04", "2026-05-05"],
                    "last_recalled_at": "2026-05-05T00:00:00+00:00",
                    "max_score": 0.9,
                }
            }
        ),
        encoding="utf-8",
    )
    (instinct / "memory_events.jsonl").write_text(
        json.dumps({"type": "memory.recall.folded", "timestamp": "2026-05-05T00:00:00"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
async def db_path(workspace: Path):
    from src.store.dashboard_writer import DashboardWriter

    db = DashboardWriter(workspace / "data" / "dashboard.db")
    await db.connect()
    await db.close()
    return workspace / "data" / "dashboard.db"


@pytest.fixture
def client(db_path: Path, workspace: Path):
    app = create_ui_app(
        db_path=db_path,
        static_dir=None,
        app_context={"workspace": workspace},
    )
    with TestClient(app) as c:
        yield c


def test_list_nodes(client):
    resp = client.get("/api/memory/nodes?type=rule")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["title"] == "Test Rule"


def test_search_nodes(client):
    resp = client.get("/api/memory/search?q=test")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


def test_get_node(client):
    nodes = client.get("/api/memory/nodes?type=rule").json()
    node_id = nodes[0]["id"]
    resp = client.get(f"/api/memory/nodes/{node_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"]["title"] == "Test Rule"
    assert "edges" in data


def test_get_node_not_found(client):
    resp = client.get("/api/memory/nodes/nonexistent")
    assert resp.status_code == 404


def test_markdown_sections(client):
    resp = client.get("/api/memory/markdown")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sections"]) >= 2
    titles = [s["title"] for s in data["sections"]]
    assert "Section One" in titles


def test_instinct_summary(client):
    resp = client.get("/api/memory/instinct")
    assert resp.status_code == 200
    data = resp.json()
    assert data["framework"]["core"]["rules"]
    assert any(d["id"] == "coding/general" for d in data["framework"]["domains"])
    assert data["runtime"]["status"]["active_rules"] == 1
    assert data["runtime"]["status"]["candidate_rules"] == 1
    assert data["runtime"]["status"]["recall_targets"] == 1
    assert data["runtime"]["rules"]["active"]["rules"][0]["id"] == "rule-a"
    assert data["runtime"]["recall"]["targets"][0]["target_id"] == "rule-a"
