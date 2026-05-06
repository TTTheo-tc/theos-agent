"""Async writer for the web dashboard SQLite database.

Owns: dashboard telemetry SQLite writes (sessions, agents, events, channel stats).
Does NOT own: agent memory, session state. Errors never block the agent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from src.safety.leak_detector import scrub_credentials

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_key TEXT,
    channel TEXT,
    started_at TEXT,
    last_activity TEXT,
    status TEXT DEFAULT 'running',
    message_count INTEGER DEFAULT 0,
    topic TEXT DEFAULT '',
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0,
    data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    name TEXT,
    status TEXT DEFAULT 'pending',
    task_state TEXT DEFAULT 'PENDING',
    model TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    started_at TEXT,
    ended_at TEXT,
    duration_ms REAL DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_hit_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    agent_id TEXT,
    event_type TEXT,
    payload TEXT DEFAULT '{}',
    timestamp TEXT
);

CREATE TABLE IF NOT EXISTS channel_stats (
    channel TEXT PRIMARY KEY,
    online INTEGER DEFAULT 0,
    messages_total INTEGER DEFAULT 0,
    messages_last_24h INTEGER DEFAULT 0,
    errors_last_24h INTEGER DEFAULT 0,
    avg_response_ms REAL DEFAULT 0,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_agents_session ON agents(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id(session_key: str) -> str:
    """Deterministic short ID from session key."""
    return hashlib.sha256(session_key.encode()).hexdigest()[:16]


class DashboardWriter:
    """Async SQLite writer for the web dashboard DB."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.execute("PRAGMA user_version = 1")
        await self._conn.commit()
        logger.info("Dashboard DB connected: {}", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    def set_event_callback(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Set an optional async callback invoked after each event INSERT."""
        self._event_callback = callback

    # -- safe wrapper ----------------------------------------------------------

    async def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if not self._conn:
            return
        try:
            await self._conn.execute(sql, params)
            await self._conn.commit()
        except Exception:
            logger.opt(exception=True).warning("Dashboard write failed")

    # -- public API ------------------------------------------------------------

    async def upsert_session(
        self,
        session_key: str,
        channel: str,
        *,
        message_count: int = 0,
        total_tokens: int = 0,
        status: str = "running",
    ) -> str:
        sid = _session_id(session_key)
        now = _now()
        await self._exec(
            """INSERT INTO sessions (id, session_key, channel, started_at, last_activity, status, message_count, total_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 last_activity = ?, status = ?, message_count = ?, total_tokens = ?""",
            (
                sid,
                session_key,
                channel,
                now,
                now,
                status,
                message_count,
                total_tokens,
                now,
                status,
                message_count,
                total_tokens,
            ),
        )
        return sid

    async def insert_agent(
        self,
        agent_id: str,
        session_key: str,
        *,
        model: str = "",
        name: str = "agent",
    ) -> None:
        sid = _session_id(session_key)
        await self._exec(
            """INSERT OR IGNORE INTO agents (id, session_id, name, status, task_state, model, started_at)
               VALUES (?, ?, ?, 'running', 'RUNNING', ?, ?)""",
            (agent_id, sid, name, model, _now()),
        )

    async def finish_agent(
        self,
        agent_id: str,
        *,
        usage: dict[str, int] | None = None,
        status: str = "completed",
        duration_ms: float = 0,
    ) -> None:
        u = usage or {}
        await self._exec(
            """UPDATE agents SET status = ?, task_state = 'DONE', ended_at = ?,
               duration_ms = ?, input_tokens = ?, output_tokens = ?, cache_hit_tokens = ?
               WHERE id = ?""",
            (
                status,
                _now(),
                duration_ms,
                u.get("input_tokens", 0),
                u.get("output_tokens", 0),
                u.get("cache_read_input_tokens", 0),
                agent_id,
            ),
        )

    async def emit_event(
        self,
        session_key: str,
        event_type: str,
        *,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        sid = _session_id(session_key)
        scrubbed_payload = scrub_credentials(json.dumps(payload or {}))
        now = _now()
        if not self._conn:
            return
        try:
            cursor = await self._conn.execute(
                """INSERT INTO events (session_id, agent_id, event_type, payload, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (sid, agent_id, event_type, scrubbed_payload, now),
            )
            await self._conn.commit()
            if self._event_callback and cursor.lastrowid:
                try:
                    await self._event_callback(
                        {
                            "id": cursor.lastrowid,
                            "session_id": sid,
                            "agent_id": agent_id,
                            "event_type": event_type,
                            "payload": scrubbed_payload,
                            "timestamp": now,
                        }
                    )
                except Exception:
                    logger.opt(exception=True).debug("Event callback failed")
        except Exception:
            logger.opt(exception=True).warning("Dashboard write failed")

    async def upsert_channel_stat(
        self,
        channel: str,
        *,
        online: bool = True,
    ) -> None:
        await self._exec(
            """INSERT INTO channel_stats (channel, online, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(channel) DO UPDATE SET online = ?, updated_at = ?""",
            (channel, int(online), _now(), int(online), _now()),
        )
