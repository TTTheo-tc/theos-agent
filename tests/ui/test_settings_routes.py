from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.store.dashboard_writer import DashboardWriter
from src.ui.server import create_ui_app


@pytest.fixture
async def db_path(tmp_path: Path):
    db = DashboardWriter(tmp_path / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "dashboard.db"


@pytest.fixture
def client(db_path: Path, tmp_path: Path):
    app = create_ui_app(db_path=db_path, static_dir=None, app_context={"workspace": tmp_path})
    with TestClient(app) as c:
        yield c


def test_get_default_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["theme"] == "dark"
    assert data["refresh_interval_ms"] == 10000


def test_put_settings(client):
    resp = client.put("/api/settings", json={"theme": "light"})
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["theme"] == "light"
    assert resp.json()["refresh_interval_ms"] == 10000


def test_settings_persist(client, tmp_path: Path):
    client.put("/api/settings", json={"sidebar_collapsed": True})
    settings_file = tmp_path / "data" / "ui-settings.json"
    assert settings_file.exists()
