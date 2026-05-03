from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.cron.service import CronService
from src.cron.types import CronSchedule
from src.store.dashboard_writer import DashboardWriter
from src.ui.server import create_ui_app


@pytest.fixture
def cron_service(tmp_path: Path):
    store_path = tmp_path / "cron" / "jobs.json"
    svc = CronService(store_path)
    svc.add_job("test-job", CronSchedule(kind="every", every_ms=3600000), "hello")
    return svc


@pytest.fixture
async def db_path(tmp_path: Path):
    db = DashboardWriter(tmp_path / "dashboard.db")
    await db.connect()
    await db.close()
    return tmp_path / "dashboard.db"


@pytest.fixture
def client(db_path: Path, cron_service: CronService):
    app = create_ui_app(
        db_path=db_path, static_dir=None, app_context={"cron_service": cron_service}
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def standalone_client(db_path: Path, tmp_path: Path):
    store_path = tmp_path / "cron" / "jobs.json"
    svc = CronService(store_path)
    svc.add_job("file-job", CronSchedule(kind="every", every_ms=60000), "ping")
    app = create_ui_app(
        db_path=db_path, static_dir=None, app_context={"cron_store_path": store_path}
    )
    with TestClient(app) as c:
        yield c


def test_list_jobs(client):
    resp = client.get("/api/cron/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "test-job"


def test_list_jobs_standalone(standalone_client):
    resp = standalone_client.get("/api/cron/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


def test_toggle_job(client):
    jobs = client.get("/api/cron/jobs").json()
    job_id = jobs[0]["id"]
    resp = client.put(f"/api/cron/jobs/{job_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_delete_job(client):
    jobs = client.get("/api/cron/jobs").json()
    job_id = jobs[0]["id"]
    resp = client.delete(f"/api/cron/jobs/{job_id}")
    assert resp.status_code == 200
    assert client.get("/api/cron/jobs").json() == []


def test_write_ops_503_standalone(standalone_client):
    resp = standalone_client.post(
        "/api/cron/jobs",
        json={"name": "x", "schedule": {"kind": "every", "everyMs": 1000}, "message": "y"},
    )
    assert resp.status_code == 503
