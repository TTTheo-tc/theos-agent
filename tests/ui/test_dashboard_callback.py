from __future__ import annotations

from pathlib import Path

import pytest

from src.store.dashboard_writer import DashboardWriter


@pytest.fixture
async def writer(tmp_path: Path):
    w = DashboardWriter(tmp_path / "test.db")
    await w.connect()
    yield w
    await w.close()


async def test_emit_event_calls_callback(writer: DashboardWriter):
    """emit_event should invoke the callback with a complete event record including id."""
    received = []

    async def on_event(event: dict):
        received.append(event)

    writer.set_event_callback(on_event)

    sid = await writer.upsert_session("test:key", "cli")
    await writer.emit_event("test:key", "agent_started", agent_id="a1", payload={"name": "main"})

    assert len(received) == 1
    evt = received[0]
    assert isinstance(evt["id"], int)
    assert evt["id"] > 0
    assert evt["session_id"] == sid
    assert evt["agent_id"] == "a1"
    assert evt["event_type"] == "agent_started"
    assert "name" in evt["payload"]
    assert evt["timestamp"]


async def test_emit_event_works_without_callback(writer: DashboardWriter):
    """emit_event still works when no callback is set."""
    await writer.upsert_session("test:key", "cli")
    await writer.emit_event("test:key", "task_created")
    # No error raised — success


async def test_emit_event_serializes_non_json_payload_values(writer: DashboardWriter):
    """Non-JSON-native telemetry payloads should not break best-effort writes."""
    received = []

    async def on_event(event: dict):
        received.append(event)

    writer.set_event_callback(on_event)
    await writer.upsert_session("test:key", "cli")
    await writer.emit_event(
        "test:key",
        "path_seen",
        payload={"path": Path("/tmp/example.txt")},
    )

    assert len(received) == 1
    assert '"/tmp/example.txt"' in received[0]["payload"]


async def test_callback_error_does_not_block(writer: DashboardWriter):
    """A failing callback must not prevent the event from being written."""

    async def bad_callback(event: dict):
        raise RuntimeError("callback exploded")

    writer.set_event_callback(bad_callback)
    await writer.upsert_session("test:key", "cli")
    await writer.emit_event("test:key", "agent_started")
    # No error raised, AND the event was actually written to DB
    import aiosqlite

    async with aiosqlite.connect(str(writer._db_path)) as conn:
        cursor = await conn.execute(
            "SELECT event_type FROM events WHERE event_type = 'agent_started'"
        )
        row = await cursor.fetchone()
        assert row is not None, "Event should be persisted despite callback failure"


async def test_connect_clears_connection_when_schema_init_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = DashboardWriter(tmp_path / "test.db")
    opened = _FakeConnection()

    async def connect(_path: str) -> "_FakeConnection":
        return opened

    monkeypatch.setattr("src.store.dashboard_writer.aiosqlite.connect", connect)

    with pytest.raises(RuntimeError, match="schema failed"):
        await writer.connect()

    assert opened.closed is True
    assert writer._conn is None


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    async def execute(self, sql: str) -> None:
        if "journal_mode" in sql:
            return None
        raise RuntimeError("schema failed")

    async def executescript(self, _sql: str) -> None:
        raise RuntimeError("schema failed")

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
