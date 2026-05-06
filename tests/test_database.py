"""Tests for the shared async SQLite wrapper."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.store.database import Database


class _FakeConnection:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.commits = 0

    async def executescript(self, sql: str) -> None:
        self.scripts.append(sql)

    async def commit(self) -> None:
        self.commits += 1


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
