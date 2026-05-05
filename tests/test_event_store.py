"""Tests for EventStore — append-only task event log."""

from datetime import datetime
from pathlib import Path

import pytest

from src.store.database import Database
from src.store.event_store import EventStore


@pytest.fixture
async def store(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    es = EventStore(db)
    yield es
    await db.close()


async def test_append_and_get_events(store: EventStore):
    await store.append(
        "t1",
        "sess1",
        {
            "type": "created",
            "new_state": "pending",
            "timestamp": "2025-01-01T00:00:00",
        },
    )
    await store.append(
        "t1",
        "sess1",
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-01-01T00:01:00",
        },
    )
    events = await store.get_events("t1")
    assert len(events) == 2
    assert events[0]["event_type"] == "created"
    assert events[1]["event_type"] == "transition"
    assert events[1]["old_state"] == "pending"
    assert events[1]["new_state"] == "executing"


async def test_append_batch(store: EventStore):
    events = [
        {"type": "created", "new_state": "pending", "timestamp": "2025-01-01T00:00:00"},
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-01-01T00:01:00",
        },
        {
            "type": "transition",
            "old_state": "executing",
            "new_state": "approved",
            "timestamp": "2025-01-01T00:02:00",
            "metadata": {"review_round": 1},
        },
    ]
    await store.append_batch("t2", "sess1", events)
    result = await store.get_events("t2")
    assert len(result) == 3
    assert result[2]["metadata"] == {"review_round": 1}


async def test_append_preserves_metadata(store: EventStore):
    await store.append(
        "t-meta",
        "sess1",
        {
            "type": "transition",
            "new_state": "approved",
            "timestamp": "2025-01-01T00:00:00",
            "metadata": {"reviewer": "verifier", "attempt": 2},
        },
    )

    result = await store.get_events("t-meta")

    assert result[0]["metadata"] == {"reviewer": "verifier", "attempt": 2}


async def test_get_events_by_session(store: EventStore):
    await store.append("t1", "sess1", {"type": "created", "timestamp": "2025-01-01T00:00:00"})
    await store.append("t2", "sess1", {"type": "created", "timestamp": "2025-01-01T01:00:00"})
    await store.append("t3", "sess2", {"type": "created", "timestamp": "2025-01-01T00:00:00"})

    sess1 = await store.get_events_by_session("sess1")
    assert len(sess1) == 2

    sess2 = await store.get_events_by_session("sess2")
    assert len(sess2) == 1


async def test_get_events_by_session_since(store: EventStore):
    await store.append("t1", "sess1", {"type": "a", "timestamp": "2025-01-01T00:00:00"})
    await store.append("t2", "sess1", {"type": "b", "timestamp": "2025-01-01T02:00:00"})

    since = datetime(2025, 1, 1, 1, 0, 0)
    result = await store.get_events_by_session("sess1", since=since)
    assert len(result) == 1
    assert result[0]["event_type"] == "b"


async def test_get_latest_state(store: EventStore):
    await store.append(
        "t1",
        "sess1",
        {
            "type": "created",
            "new_state": "pending",
            "timestamp": "2025-01-01T00:00:00",
        },
    )
    await store.append(
        "t1",
        "sess1",
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-01-01T00:01:00",
        },
    )
    latest = await store.get_latest_state("t1")
    assert latest is not None
    assert latest["new_state"] == "executing"


async def test_empty_results(store: EventStore):
    events = await store.get_events("nonexistent")
    assert events == []

    latest = await store.get_latest_state("nonexistent")
    assert latest is None

    sess = await store.get_events_by_session("nonexistent")
    assert sess == []
