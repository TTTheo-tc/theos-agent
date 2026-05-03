from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.config.loader import load_config, save_config
from src.config.schema import Config
from src.store.dashboard_writer import DashboardWriter
from src.ui.server import create_ui_app


@pytest.fixture
def config_path(tmp_path: Path):
    path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.model = "anthropic/claude-sonnet-4"
    config.providers.anthropic.api_key = "sk-ant-real-secret"
    save_config(config, path)
    return path


@pytest.fixture
async def db_path(tmp_path: Path):
    db = DashboardWriter(tmp_path / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "dashboard.db"


@pytest.fixture
def client(db_path: Path, config_path: Path):
    config = load_config(config_path)
    app = create_ui_app(
        db_path=db_path,
        static_dir=None,
        app_context={"config": config, "config_path": config_path},
    )
    with TestClient(app) as c:
        yield c


def test_get_config_redacts_secrets(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"]["anthropic"]["apiKey"] == "***"
    assert data["agents"]["defaults"]["model"] == "anthropic/claude-sonnet-4"


def test_put_config_preserves_secrets(client, config_path):
    """PUT with *** values should NOT overwrite real secrets."""
    resp = client.get("/api/config")
    data = resp.json()
    data["agents"]["defaults"]["model"] = "anthropic/claude-opus-4"
    resp = client.put("/api/config", json=data)
    assert resp.status_code == 200

    raw = json.loads(config_path.read_text())
    assert raw["agents"]["defaults"]["model"] == "anthropic/claude-opus-4"
    api_key = raw["providers"]["anthropic"]["apiKey"]
    assert api_key != "***"


def test_put_config_503_standalone(tmp_path: Path):
    import asyncio

    async def setup():
        db = DashboardWriter(tmp_path / "dashboard.db")
        await db.connect()
        await db.close()
        return tmp_path / "dashboard.db"

    dp = asyncio.run(setup())
    app = create_ui_app(db_path=dp, static_dir=None, app_context=None)
    with TestClient(app) as c:
        resp = c.put("/api/config", json={"agents": {"defaults": {"model": "x"}}})
    assert resp.status_code == 503
