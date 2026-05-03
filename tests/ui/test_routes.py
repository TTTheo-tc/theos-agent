from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.session.subagent_store import SubagentStore
from src.session.turn_store import TurnStore
from src.store.dashboard_writer import DashboardWriter
from src.ui.db import DashboardReader
from src.ui.server import create_ui_app


@pytest.fixture
async def db_path(tmp_path: Path):
    w = DashboardWriter(tmp_path / "dash.db")
    await w.connect()
    await w.upsert_session("telegram:chat1", "telegram", message_count=3)
    await w.upsert_session("cli:plain", "cli", message_count=1)
    await w.insert_agent("a1", "telegram:chat1", model="claude-sonnet-4", name="main")
    await w.finish_agent("a1", usage={"input_tokens": 100, "output_tokens": 50}, duration_ms=500)
    await w.emit_event("telegram:chat1", "agent_started", agent_id="a1")
    await w.upsert_channel_stat("telegram", online=True)
    await w.close()

    turns = TurnStore(tmp_path)
    turns.record("telegram:chat1", "turn-1", "waiting_user", question="Need clarification")
    subagents = SubagentStore(tmp_path)
    subagents.record("telegram:chat1", "sub-1", "running", label="explore repo", role="explorer")
    return tmp_path / "dash.db"


@pytest.fixture
def client(db_path: Path):
    app = create_ui_app(db_path=db_path, static_dir=None)
    with TestClient(app) as c:
        yield c


def test_get_sessions(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    recoverable = next(row for row in data if row["session_key"] == "telegram:chat1")
    assert recoverable["channel"] == "telegram"
    assert recoverable["recoverable"] is True
    assert recoverable["runtime_state"] == "waiting_user"
    assert recoverable["background_task_count"] == 1
    assert "Reply in the same session" in recoverable["next_step"]


def test_get_sessions_recoverable_only(client):
    resp = client.get("/api/sessions?recoverable_only=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["session_key"] == "telegram:chat1"


def test_get_sessions_with_limit(client):
    resp = client.get("/api/sessions?limit=5")
    assert resp.status_code == 200


def test_get_session_by_id(client):
    sessions = client.get("/api/sessions").json()
    sid = next(row["id"] for row in sessions if row["session_key"] == "telegram:chat1")
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sid
    assert len(data["agents"]) == 1
    assert data["recoverable"] is True
    assert data["latest_turn"]["status"] == "waiting_user"
    assert data["background_tasks"]["active_count"] == 1
    assert data["background_tasks"]["recent"][0]["label"] == "explore repo"


def test_get_session_not_found(client):
    resp = client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


def test_get_agents(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "main"


def test_get_metrics(client):
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_sessions" in data
    assert "messages_today" in data
    assert "cost_today" in data
    assert "avg_latency_ms" in data


def test_get_cost_metrics(client):
    resp = client.get("/api/metrics/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert "daily" in data
    assert "cache_hit_rate" in data
    assert "top_sessions" in data


def test_get_channels(client):
    resp = client.get("/api/channels")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["channel"] == "telegram"


def test_search(client):
    resp = client.get("/api/search?q=telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


def test_search_empty_query(client):
    resp = client.get("/api/search?q=")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_dashboard_reader_recoverable_only_filters_before_limit(db_path: Path):
    reader = DashboardReader(db_path)
    await reader.connect()
    try:
        rows = await reader.get_sessions(limit=1, recoverable_only=True)
        assert len(rows) == 1
        assert rows[0]["session_key"] == "telegram:chat1"
        assert rows[0]["recoverable"] is True
    finally:
        await reader.close()


async def test_sse_initial_replay(db_path: Path):
    """SSE endpoint should replay existing events on connect via the generate function."""
    from unittest.mock import AsyncMock, MagicMock

    from src.ui.db import DashboardReader

    reader = DashboardReader(db_path)
    await reader.connect()
    try:
        # Build a minimal fake request/app to invoke events_stream
        app_state = MagicMock()
        app_state.db = reader
        app_state.event_bus = None

        mock_app = MagicMock()
        mock_app.state = app_state

        mock_request = MagicMock()
        mock_request.app = mock_app
        mock_request.query_params = {"last_event_id": "0"}
        mock_request.is_disconnected = AsyncMock(return_value=True)  # stop after initial batch

        from src.ui.routes import events_stream

        resp = await events_stream(mock_request)
        assert resp.media_type == "text/event-stream"

        # Collect yielded chunks from the generator
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
            if any("data:" in c for c in chunks):
                break

        combined = "".join(chunks)
        data_lines = [ln for ln in combined.splitlines() if ln.startswith("data:")]
        assert len(data_lines) >= 1
        event = json.loads(data_lines[0].removeprefix("data: "))
        assert "event_type" in event
        assert "id" in event
    finally:
        await reader.close()
