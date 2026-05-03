from __future__ import annotations

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
