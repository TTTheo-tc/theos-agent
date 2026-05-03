"""Event sourcing store — append-only log of TaskRecord lifecycle events."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.store.database import Database


class EventStore:
    """Append-only event log backed by the ``task_events`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(
        self,
        task_id: str,
        session_key: str,
        event: dict[str, Any],
    ) -> None:
        """Insert a single event."""
        await self._db.execute(
            "INSERT INTO task_events (task_id, session_key, event_type, old_state, new_state, timestamp, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                session_key,
                event.get("type", "unknown"),
                event.get("old_state"),
                event.get("new_state"),
                event.get("timestamp", datetime.now().isoformat()),
                json.dumps(event.get("metadata", {})),
            ),
        )

    async def append_batch(
        self,
        task_id: str,
        session_key: str,
        events: list[dict[str, Any]],
    ) -> None:
        """Insert multiple events in a single transaction."""
        if not events:
            return
        await self._db.execute_many(
            "INSERT INTO task_events (task_id, session_key, event_type, old_state, new_state, timestamp, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    task_id,
                    session_key,
                    e.get("type", "unknown"),
                    e.get("old_state"),
                    e.get("new_state"),
                    e.get("timestamp", datetime.now().isoformat()),
                    json.dumps(e.get("metadata", {})),
                )
                for e in events
            ],
        )

    async def get_events(self, task_id: str) -> list[dict[str, Any]]:
        """Return all events for a task, ordered by id."""
        rows = await self._db.fetchall(
            "SELECT id, task_id, session_key, event_type, old_state, new_state, timestamp, metadata"
            " FROM task_events WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
        return [self._row_to_dict(r) for r in rows]

    async def get_events_by_session(
        self,
        session_key: str,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return events for a session, optionally filtered by time."""
        if since:
            rows = await self._db.fetchall(
                "SELECT id, task_id, session_key, event_type, old_state, new_state, timestamp, metadata"
                " FROM task_events WHERE session_key = ? AND timestamp >= ? ORDER BY id",
                (session_key, since.isoformat()),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT id, task_id, session_key, event_type, old_state, new_state, timestamp, metadata"
                " FROM task_events WHERE session_key = ? ORDER BY id",
                (session_key,),
            )
        return [self._row_to_dict(r) for r in rows]

    async def get_latest_state(self, task_id: str) -> dict[str, Any] | None:
        """Return the last transition event's new_state for a task."""
        row = await self._db.fetchone(
            "SELECT id, task_id, session_key, event_type, old_state, new_state, timestamp, metadata"
            " FROM task_events WHERE task_id = ? AND new_state IS NOT NULL ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        return self._row_to_dict(row) if row else None

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "task_id": row[1],
            "session_key": row[2],
            "event_type": row[3],
            "old_state": row[4],
            "new_state": row[5],
            "timestamp": row[6],
            "metadata": json.loads(row[7]) if row[7] else {},
        }
