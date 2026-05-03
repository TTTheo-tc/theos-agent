from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.store.dashboard_writer import DashboardWriter
from src.ui.server import create_ui_app


@pytest.fixture
async def workspace(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "gateway.log"
    lines = [
        json.dumps(
            {
                "text": "INFO | Starting gateway\n",
                "record": {
                    "level": {"name": "INFO"},
                    "message": "Starting gateway",
                    "time": {"repr": "2026-03-28T10:00:00"},
                },
            }
        ),
        json.dumps(
            {
                "text": "DEBUG | Loading config\n",
                "record": {
                    "level": {"name": "DEBUG"},
                    "message": "Loading config",
                    "time": {"repr": "2026-03-28T10:00:01"},
                },
            }
        ),
        json.dumps(
            {
                "text": "WARNING | api_key=sk-ant-secret123\n",
                "record": {
                    "level": {"name": "WARNING"},
                    "message": "api_key=sk-ant-secret123",
                    "time": {"repr": "2026-03-28T10:00:02"},
                },
            }
        ),
    ]
    log_file.write_text("\n".join(lines) + "\n")
    return tmp_path


@pytest.fixture
async def db_path(workspace: Path):
    db = DashboardWriter(workspace / "data" / "dashboard.db")
    await db.connect()
    await db.close()
    return workspace / "data" / "dashboard.db"


@pytest.fixture
def client(db_path: Path, workspace: Path):
    app = create_ui_app(db_path=db_path, static_dir=None, app_context={"workspace": workspace})
    with TestClient(app) as c:
        yield c


def test_get_logs(client):
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


def test_filter_by_level(client):
    resp = client.get("/api/logs?level=INFO")
    assert resp.status_code == 200
    data = resp.json()
    assert all(entry["level"] == "INFO" for entry in data)


def test_logs_are_sanitized(client):
    """Secrets in log output must be scrubbed."""
    resp = client.get("/api/logs?level=WARNING")
    assert resp.status_code == 200
    data = resp.json()
    for entry in data:
        assert "sk-ant-secret123" not in entry["message"]


def test_search_logs(client):
    resp = client.get("/api/logs?q=gateway")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.fixture
async def no_logs_db(tmp_path: Path):
    db = DashboardWriter(tmp_path / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "dashboard.db"


def test_no_logs_file(no_logs_db: Path, tmp_path: Path):
    """When no log file exists, return empty list."""
    app = create_ui_app(db_path=no_logs_db, static_dir=None, app_context={"workspace": tmp_path})
    with TestClient(app) as c:
        resp = c.get("/api/logs")
    assert resp.status_code == 200
    assert resp.json() == []
