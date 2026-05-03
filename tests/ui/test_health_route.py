from __future__ import annotations

import os
import sqlite3

from starlette.testclient import TestClient

from src.ui.server import create_ui_app


def test_health_route_returns_current_pid(tmp_path):
    db_path = tmp_path / "test.db"
    sqlite3.connect(db_path).close()

    app = create_ui_app(db_path=db_path, static_dir=None)

    with TestClient(app) as client:
        resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pid": os.getpid()}
