"""Async SQLite wrapper — shared foundation for event store and memory tiers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

_SCHEMA_SQL = """
-- Event sourcing
CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    session_key TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    old_state   TEXT,
    new_state   TEXT,
    timestamp   TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_te_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_te_session ON task_events(session_key);
CREATE INDEX IF NOT EXISTS idx_te_ts ON task_events(timestamp);

-- Short-term memory
CREATE TABLE IF NOT EXISTS memory_short_term (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key  TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    metadata     TEXT NOT NULL DEFAULT '{}',
    consolidated BOOLEAN NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mst_session ON memory_short_term(session_key);
CREATE INDEX IF NOT EXISTS idx_mst_ts ON memory_short_term(timestamp);
"""


class Database:
    """Thin async wrapper around a single SQLite file with WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the database and ensure the schema exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.db_path))
        self._conn = conn
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await self._ensure_schema()
        except Exception:
            try:
                await conn.close()
            finally:
                self._conn = None
            raise
        logger.debug("Database connected: {}", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.debug("Database closed: {}", self.db_path)

    def _check_conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Execute a write statement under the write lock."""
        conn = self._check_conn()
        async with self._write_lock:
            await conn.execute(sql, params)
            await conn.commit()

    async def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        """Execute a statement for each param tuple under the write lock."""
        conn = self._check_conn()
        async with self._write_lock:
            await conn.executemany(sql, params_seq)
            await conn.commit()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        """Execute a read query and return all rows."""
        conn = self._check_conn()
        async with conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        """Execute a read query and return the first row."""
        conn = self._check_conn()
        async with conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def executescript(self, sql: str) -> None:
        """Execute multi-statement DDL under the write lock."""
        conn = self._check_conn()
        async with self._write_lock:
            await conn.executescript(sql)
            await conn.commit()

    async def set_row_factory(self) -> None:
        """Enable dict-like row access via aiosqlite.Row."""
        conn = self._check_conn()
        conn.row_factory = aiosqlite.Row

    async def load_extension(self, path: str) -> bool:
        """Load a SQLite extension. Returns False on failure (logged, not raised)."""
        conn = self._check_conn()
        try:
            await conn.enable_load_extension(True)
            await conn.load_extension(path)
            return True
        except Exception as e:
            logger.warning("Failed to load SQLite extension {}: {}", path, e)
            return False

    async def _ensure_schema(self) -> None:
        """Create tables and indexes if they don't exist (idempotent)."""
        await self.executescript(_SCHEMA_SQL)
