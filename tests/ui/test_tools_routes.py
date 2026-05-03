from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from src.store.dashboard_writer import DashboardWriter
from src.ui.server import create_ui_app


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.tool_names = ["read_file", "bash", "web_search"]
    tool_mock = MagicMock()
    tool_mock.name = "read_file"
    tool_mock.description = "Read a file"
    tool_mock.risk_level = "low"
    tool_mock.owner_only = False
    tool_mock.to_schema.return_value = {
        "type": "function",
        "function": {"name": "read_file", "description": "Read a file", "parameters": {}},
    }
    registry.get.return_value = tool_mock
    return registry


@pytest.fixture
async def db_path(tmp_path: Path):
    db = DashboardWriter(tmp_path / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "dashboard.db"


@pytest.fixture
def client(db_path: Path, mock_registry):
    app = create_ui_app(
        db_path=db_path, static_dir=None, app_context={"tool_registry": mock_registry}
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def standalone_client(db_path: Path):
    app = create_ui_app(db_path=db_path, static_dir=None, app_context=None)
    with TestClient(app) as c:
        yield c


def test_list_tools_gateway(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tools"]) >= 1
    assert data["tools"][0]["name"] == "read_file"
    assert "risk_level" in data["tools"][0]


def test_list_tools_standalone(standalone_client):
    resp = standalone_client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    assert "full" in data["profiles"]


def test_get_profiles(client):
    resp = client.get("/api/tools/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    assert "groups" in data
