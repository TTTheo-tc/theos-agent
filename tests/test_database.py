"""Tests for the shared async SQLite wrapper."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.store.database import Database


class _FakeConnection:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.commits = 0
        self.closed = False

    async def executescript(self, sql: str) -> None:
        self.scripts.append(sql)

    async def execute(self, _sql: str) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def close(self) -> None:
        self.closed = True


async def test_executescript_commits_after_running_script(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    conn = _FakeConnection()
    db._conn = conn  # type: ignore[assignment]

    await db.executescript("CREATE TABLE scratch (value TEXT)")

    assert conn.scripts == ["CREATE TABLE scratch (value TEXT)"]
    assert conn.commits == 1


async def test_connect_initializes_schema_without_deadlock(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        await asyncio.wait_for(db.connect(), timeout=1.0)
        row = await db.fetchone(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'task_events'"
        )

        assert row is not None
    finally:
        await db.close()


async def test_connect_clears_connection_when_schema_init_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(tmp_path / "test.db")
    opened = _FakeConnection()

    async def connect(_path: str) -> _FakeConnection:
        return opened

    async def fail_schema() -> None:
        raise RuntimeError("schema failed")

    monkeypatch.setattr("src.store.database.aiosqlite.connect", connect)
    monkeypatch.setattr(db, "_ensure_schema", fail_schema)

    with pytest.raises(RuntimeError, match="schema failed"):
        await db.connect()

    assert opened.closed is True
    assert db._conn is None
