from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.ui.server import create_ui_app


@pytest.fixture
async def db_path(tmp_path: Path):
    from src.store.dashboard_writer import DashboardWriter

    db = DashboardWriter(tmp_path / "data" / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "data" / "dashboard.db"


@pytest.fixture
def client(db_path: Path, tmp_path: Path):
    app = create_ui_app(
        db_path=db_path,
        static_dir=None,
        app_context={"workspace": tmp_path},
    )
    with TestClient(app) as c:
        yield c


def test_wiki_status_before_init(client, tmp_path: Path):
    resp = client.get("/api/wiki/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is False
    assert data["root"] == str(tmp_path / "llm-wiki")
    assert data["files"] == []


def test_wiki_init_creates_structure(client, tmp_path: Path):
    resp = client.post("/api/wiki/init")

    assert resp.status_code == 201
    data = resp.json()
    assert data["initialized"] is True
    assert (tmp_path / "llm-wiki" / "raw").is_dir()
    assert (tmp_path / "llm-wiki" / "wiki" / "concepts").is_dir()
    assert (tmp_path / "llm-wiki" / "wiki" / "entities").is_dir()
    assert (tmp_path / "llm-wiki" / "wiki" / "sources").is_dir()
    assert (tmp_path / "llm-wiki" / "wiki" / "outputs").is_dir()
    assert (tmp_path / "llm-wiki" / "CLAUDE.md").is_file()
    assert {file["path"] for file in data["files"]} >= {
        "CLAUDE.md",
        "wiki/index.md",
        "wiki/log.md",
    }
    assert data["log"][0]["kind"] == "init"


def test_wiki_page_reads_markdown(client):
    client.post("/api/wiki/init")

    resp = client.get("/api/wiki/page?path=wiki/index.md")

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "wiki/index.md"
    assert "# Index" in data["content"]


def test_wiki_page_blocks_traversal(client):
    client.post("/api/wiki/init")

    resp = client.get("/api/wiki/page?path=../secret.md")

    assert resp.status_code == 403


def test_wiki_search_returns_matches(client, tmp_path: Path):
    client.post("/api/wiki/init")
    concept = tmp_path / "llm-wiki" / "wiki" / "concepts" / "attention.md"
    concept.write_text(
        "---\ntags: [transformer]\ndate: 2026-05-05\nsources: []\n---\n\n"
        "# Attention\n\nAttention routes token context through weighted retrieval.\n",
        encoding="utf-8",
    )

    resp = client.get("/api/wiki/search?q=weighted")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["path"] == "wiki/concepts/attention.md"
    assert "weighted retrieval" in data[0]["snippet"]


def test_wiki_record_creates_page_index_and_log(client, tmp_path: Path):
    resp = client.post(
        "/api/wiki/record",
        json={
            "category": "concepts",
            "title": "Attention Routing",
            "summary": "How attention moves context through tokens.",
            "body": "Connect this to [[Transformer]] and KV cache notes.",
            "tags": "llm, transformer",
            "sources": ["raw/attention.md"],
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["file"]["path"] == "wiki/concepts/attention-routing.md"
    assert data["status"]["counts"]["concepts"] == 1

    page = tmp_path / "llm-wiki" / "wiki" / "concepts" / "attention-routing.md"
    text = page.read_text(encoding="utf-8")
    assert 'tags: ["wiki/concept", "llm", "transformer"]' in text
    assert 'sources: ["raw/attention.md"]' in text
    assert "# Attention Routing" in text
    assert "## Notes" in text

    index = (tmp_path / "llm-wiki" / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[concepts/attention-routing|Attention Routing]]" in index
    assert "How attention moves context through tokens." in index

    log = (tmp_path / "llm-wiki" / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "## [" in log
    assert "note | Attention Routing" in log


def test_wiki_record_rejects_invalid_category(client):
    resp = client.post(
        "/api/wiki/record",
        json={"category": "raw", "title": "Do not write raw"},
    )

    assert resp.status_code == 400
