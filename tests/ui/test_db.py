from __future__ import annotations

from pathlib import Path

import pytest

from src.store.dashboard_writer import DashboardWriter
from src.ui.db import DashboardReader


@pytest.fixture
async def db_path(tmp_path: Path):
    """Create a DB with schema via DashboardWriter, return path."""
    w = DashboardWriter(tmp_path / "dash.db")
    await w.connect()
    await w.upsert_session("telegram:chat1", "telegram", message_count=5)
    await w.insert_agent("agent-1", "telegram:chat1", model="claude-sonnet-4", name="main")
    await w.finish_agent(
        "agent-1",
        usage={"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 200},
        duration_ms=1500.0,
    )
    await w.emit_event("telegram:chat1", "agent_started", agent_id="agent-1")
    await w.emit_event("telegram:chat1", "agent_completed", agent_id="agent-1")
    await w.upsert_channel_stat("telegram", online=True)
    await w.close()
    return tmp_path / "dash.db"


@pytest.fixture
async def reader(db_path: Path):
    r = DashboardReader(db_path)
    await r.connect()
    yield r
    await r.close()


async def test_get_sessions(reader: DashboardReader):
    sessions = await reader.get_sessions(limit=10)
    assert len(sessions) == 1
    assert sessions[0]["channel"] == "telegram"
    assert sessions[0]["message_count"] == 5


async def test_get_session(reader: DashboardReader, db_path: Path):
    sessions = await reader.get_sessions()
    sid = sessions[0]["id"]
    session = await reader.get_session(sid)
    assert session is not None
    assert session["channel"] == "telegram"


async def test_get_session_not_found(reader: DashboardReader):
    session = await reader.get_session("nonexistent")
    assert session is None


async def test_get_agents(reader: DashboardReader):
    agents = await reader.get_agents(limit=10)
    assert len(agents) == 1
    assert agents[0]["name"] == "main"
    assert agents[0]["model"] == "claude-sonnet-4"


async def test_get_agents_by_session(reader: DashboardReader):
    sessions = await reader.get_sessions()
    sid = sessions[0]["id"]
    agents = await reader.get_agents_by_session(sid)
    assert len(agents) == 1
    assert agents[0]["id"] == "agent-1"


async def test_get_events(reader: DashboardReader):
    events = await reader.get_events(since_id=0, limit=10)
    assert len(events) == 2
    types = {e["event_type"] for e in events}
    assert "agent_started" in types
    assert "agent_completed" in types


async def test_get_events_since_id(reader: DashboardReader):
    all_events = await reader.get_events(since_id=0, limit=10)
    first_id = min(e["id"] for e in all_events)
    filtered = await reader.get_events(since_id=first_id, limit=10)
    assert len(filtered) == 1


async def test_get_channel_stats(reader: DashboardReader):
    stats = await reader.get_channel_stats()
    assert len(stats) == 1
    assert stats[0]["channel"] == "telegram"
    assert stats[0]["online"] == 1


async def test_get_metrics(reader: DashboardReader):
    metrics = await reader.get_metrics()
    assert "active_sessions" in metrics
    assert "messages_today" in metrics
    assert "avg_latency_ms" in metrics
    assert "cost_today" in metrics


async def test_get_cost_metrics(reader: DashboardReader):
    cost = await reader.get_cost_metrics()
    assert "daily" in cost
    assert "cache_hit_rate" in cost
    assert "top_sessions" in cost


async def test_search(reader: DashboardReader):
    results = await reader.search("telegram")
    assert len(results) >= 1
