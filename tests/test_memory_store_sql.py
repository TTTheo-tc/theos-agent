"""Tests for ShortTermMemoryStore — SQLite-backed short-term memory tier.

Note: ShortTermMemoryStore is a buffer/audit layer, not the primary
consolidation input.  Consolidation reads from Session.messages.
mark_consolidated() is bookkeeping that tracks which rows have been
processed; it does not make SQLite the archive source.
"""

from pathlib import Path

import pytest

from src.memory.sql import ShortTermMemoryStore
from src.store.database import Database


@pytest.fixture
async def store(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    st = ShortTermMemoryStore(db)
    yield st
    await db.close()


async def test_write_and_read(store: ShortTermMemoryStore):
    messages = [
        {"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00"},
        {"role": "assistant", "content": "hi there", "timestamp": "2025-01-01T00:00:01"},
    ]
    await store.write_messages("sess1", messages)
    recent = await store.get_recent("sess1")
    assert len(recent) == 2
    assert recent[0]["role"] == "user"
    assert recent[1]["content"] == "hi there"


async def test_read_tolerates_invalid_metadata(store: ShortTermMemoryStore):
    await store.write_messages(
        "sess1",
        [{"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00"}],
    )
    recent = await store.get_recent("sess1")
    await store._db.execute(
        "UPDATE memory_short_term SET metadata = ? WHERE id = ?",
        ("{bad json", recent[0]["id"]),
    )

    reread = await store.get_recent("sess1")

    assert reread[0]["metadata"] == {}


async def test_read_ignores_non_object_metadata(store: ShortTermMemoryStore):
    await store.write_messages(
        "sess1",
        [{"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00"}],
    )
    recent = await store.get_recent("sess1")
    await store._db.execute(
        "UPDATE memory_short_term SET metadata = ? WHERE id = ?",
        ('["not", "object"]', recent[0]["id"]),
    )

    reread = await store.get_recent("sess1")

    assert reread[0]["metadata"] == {}


async def test_get_recent_respects_limit(store: ShortTermMemoryStore):
    messages = [
        {"role": "user", "content": f"msg{i}", "timestamp": f"2025-01-01T00:00:{i:02d}"}
        for i in range(10)
    ]
    await store.write_messages("sess1", messages)
    recent = await store.get_recent("sess1", limit=3)
    assert len(recent) == 3
    # Should be the 3 most recent, in chronological order
    assert recent[0]["content"] == "msg7"
    assert recent[2]["content"] == "msg9"


async def test_mark_consolidated(store: ShortTermMemoryStore):
    messages = [
        {"role": "user", "content": f"msg{i}", "timestamp": f"2025-01-01T00:00:{i:02d}"}
        for i in range(5)
    ]
    await store.write_messages("sess1", messages)

    uncons = await store.get_unconsolidated("sess1")
    assert len(uncons) == 5

    # Mark first 3 as consolidated (by id of the 3rd message)
    third_id = uncons[2]["id"]
    await store.mark_consolidated("sess1", third_id)

    remaining = await store.get_unconsolidated("sess1")
    assert len(remaining) == 2


async def test_count_unconsolidated(store: ShortTermMemoryStore):
    assert await store.count_unconsolidated("sess1") == 0

    messages = [
        {"role": "user", "content": f"msg{i}", "timestamp": f"2025-01-01T00:00:{i:02d}"}
        for i in range(4)
    ]
    await store.write_messages("sess1", messages)
    assert await store.count_unconsolidated("sess1") == 4

    uncons = await store.get_unconsolidated("sess1")
    await store.mark_consolidated("sess1", uncons[1]["id"])
    assert await store.count_unconsolidated("sess1") == 2


async def test_empty_write(store: ShortTermMemoryStore):
    await store.write_messages("sess1", [])
    recent = await store.get_recent("sess1")
    assert recent == []


async def test_session_isolation(store: ShortTermMemoryStore):
    await store.write_messages("sess1", [{"role": "user", "content": "a", "timestamp": "t1"}])
    await store.write_messages("sess2", [{"role": "user", "content": "b", "timestamp": "t2"}])

    assert len(await store.get_recent("sess1")) == 1
    assert len(await store.get_recent("sess2")) == 1
    assert (await store.get_recent("sess1"))[0]["content"] == "a"
