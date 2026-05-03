"""Read-only async access to the dashboard SQLite DB.

Schema owner: src/store/dashboard_writer.py
This module only reads — never creates tables or modifies data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from src.session.runtime_state import build_session_runtime_state
from src.session.subagent_store import SubagentStore
from src.session.turn_store import TurnStore


def _row_to_dict(cursor: aiosqlite.Cursor, row: tuple) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class DashboardReader:
    """Async read-only access to the dashboard SQLite DB."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        workspace = db_path.parent.parent if db_path.parent.name == "data" else db_path.parent
        self._turns = TurnStore(workspace)
        self._subagents = SubagentStore(workspace)

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True)
        # No WAL pragma — read-only connections inherit the journal mode
        # set by the writer. Setting it here would fail on mode=ro.

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        assert self._conn
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_dict(cursor, r) for r in rows]

    async def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        assert self._conn
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return _row_to_dict(cursor, row) if row else None

    async def get_sessions(self, limit: int = 20, recoverable_only: bool = False) -> list[dict]:
        if recoverable_only:
            rows = await self._fetchall("SELECT * FROM sessions ORDER BY last_activity DESC")
        else:
            rows = await self._fetchall(
                "SELECT * FROM sessions ORDER BY last_activity DESC LIMIT ?",
                (limit,),
            )
        enriched = [self._enrich_session_row(row) for row in rows]
        if recoverable_only:
            enriched = [row for row in enriched if row.get("recoverable")]
            return enriched[:limit]
        return enriched

    async def get_session(self, session_id: str) -> dict | None:
        row = await self._fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return self._enrich_session_row(row, detail=True) if row else None

    async def get_agents(self, limit: int = 50) -> list[dict]:
        return await self._fetchall(
            "SELECT a.*, s.topic as session_topic "
            "FROM agents a LEFT JOIN sessions s ON a.session_id = s.id "
            "ORDER BY a.started_at DESC LIMIT ?",
            (limit,),
        )

    async def get_agents_by_session(self, session_id: str) -> list[dict]:
        return await self._fetchall(
            "SELECT * FROM agents WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )

    async def get_events(
        self, since_id: int = 0, limit: int = 100, session_id: str | None = None
    ) -> list[dict]:
        if session_id:
            return await self._fetchall(
                "SELECT * FROM events WHERE id > ? AND session_id = ? " "ORDER BY id DESC LIMIT ?",
                (since_id, session_id, limit),
            )
        return await self._fetchall(
            "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
            (since_id, limit),
        )

    async def get_channel_stats(self) -> list[dict]:
        return await self._fetchall("SELECT * FROM channel_stats ORDER BY channel")

    async def get_metrics(self) -> dict:
        row = await self._fetchone("SELECT date('now') as d")
        today = row["d"] if row else "1970-01-01"
        active = await self._fetchone(
            "SELECT COUNT(*) as count FROM sessions WHERE status = 'running'"
        ) or {"count": 0}
        msgs = await self._fetchone(
            "SELECT COALESCE(SUM(message_count), 0) as count "
            "FROM sessions WHERE started_at >= ?",
            (today,),
        ) or {"count": 0}
        cost = await self._fetchone(
            "SELECT COALESCE(SUM(total_cost), 0) as cost " "FROM sessions WHERE started_at >= ?",
            (today,),
        ) or {"cost": 0}
        latency = await self._fetchone(
            "SELECT COALESCE(AVG(avg_response_ms), 0) as avg " "FROM channel_stats WHERE online = 1"
        ) or {"avg": 0}
        return {
            "active_sessions": active["count"],
            "messages_today": msgs["count"],
            "cost_today": round(cost["cost"] * 100) / 100,
            "avg_latency_ms": round(latency["avg"]),
            "cost_trend": 0,
        }

    async def get_cost_metrics(self) -> dict:
        daily = await self._fetchall(
            "SELECT date(started_at) as date, "
            "SUM(total_cost) as total, SUM(total_cost) as claude "
            "FROM sessions WHERE started_at >= date('now', '-30 days') "
            "GROUP BY date(started_at) ORDER BY date"
        )
        cache = await self._fetchone(
            "SELECT COALESCE(SUM(cache_hit_tokens), 0) as cache_tokens, "
            "COALESCE(SUM(input_tokens + output_tokens + cache_hit_tokens), 1) "
            "as total_tokens FROM agents"
        )
        hit_rate = cache["cache_tokens"] / cache["total_tokens"] if cache["total_tokens"] > 0 else 0
        top = await self._fetchall(
            "SELECT id as sessionId, topic, total_cost as cost "
            "FROM sessions ORDER BY total_cost DESC LIMIT 10"
        )
        return {
            "daily": daily,
            "by_provider": [
                {"provider": "claude", "cost": 0, "tokens": 0},
                {"provider": "openai", "cost": 0, "tokens": 0},
            ],
            "cache_hit_rate": hit_rate,
            "top_sessions": top,
        }

    async def search(self, query: str) -> list[dict]:
        pattern = f"%{query}%"
        return await self._fetchall(
            "SELECT s.*, GROUP_CONCAT(a.name) as agent_names "
            "FROM sessions s LEFT JOIN agents a ON a.session_id = s.id "
            "WHERE s.topic LIKE ? OR s.session_key LIKE ? "
            "OR s.id LIKE ? OR a.name LIKE ? "
            "GROUP BY s.id ORDER BY s.last_activity DESC LIMIT 20",
            (pattern, pattern, pattern, pattern),
        )

    def _enrich_session_row(
        self, row: dict[str, Any] | None, *, detail: bool = False
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        session_key = row.get("session_key")
        if not session_key:
            return row

        runtime = build_session_runtime_state(
            session_key,
            turn_store=self._turns,
            subagent_store=self._subagents,
            recent_background_limit=5 if detail else 3,
        )
        payload: dict[str, Any] = {
            "recoverable": runtime.recoverable,
            "runtime_state": runtime.runtime_state,
            "next_step": runtime.next_step,
            "background_task_count": len(runtime.active_background),
        }

        latest = runtime.latest_turn
        if latest is not None:
            payload.update(
                {
                    "latest_turn_id": latest.turn_id,
                    "latest_turn_status": latest.status,
                    "latest_turn_timestamp": latest.timestamp,
                }
            )
            if "question" in latest.metadata:
                payload["pending_question"] = latest.metadata["question"]

        if runtime.recent_background:
            payload["recent_background_tasks"] = [
                {
                    "task_id": cp.task_id,
                    "status": cp.status,
                    "label": cp.metadata.get("label"),
                    "role": cp.metadata.get("role"),
                }
                for cp in runtime.recent_background
            ]

        if detail:
            payload["latest_turn"] = (
                {
                    "turn_id": latest.turn_id,
                    "status": latest.status,
                    "timestamp": latest.timestamp,
                    **latest.metadata,
                }
                if latest is not None
                else None
            )
            payload["background_tasks"] = {
                "active_count": len(runtime.active_background),
                "recent": [
                    {
                        "task_id": cp.task_id,
                        "status": cp.status,
                        "label": cp.metadata.get("label"),
                        "role": cp.metadata.get("role"),
                        "task": cp.metadata.get("task"),
                        "timestamp": cp.timestamp,
                    }
                    for cp in runtime.recent_background
                ],
            }

        return {**row, **payload}
