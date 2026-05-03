"""Metrics collector — reads task_events and computes aggregate statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.store.database import Database


class MetricsCollector:
    """Compute aggregate metrics from the ``task_events`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def collect(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Collect metrics for the given time range.

        Returns a dict with keys:
        - total_tasks, completed, failed, retried
        - avg_retries, retry_rate
        - events_by_type
        - sessions_active
        - time_range
        """
        where_parts: list[str] = []
        params: list[Any] = []

        if since:
            where_parts.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            where_parts.append("timestamp <= ?")
            params.append(until.isoformat())

        where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Total unique tasks
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT task_id) FROM task_events{where_clause}",
            tuple(params),
        )
        total_tasks = row[0] if row else 0

        # Completed (new_state = 'approved')
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT task_id) FROM task_events"
            f"{' WHERE ' + ' AND '.join(where_parts + ['new_state = ?']) if where_parts else ' WHERE new_state = ?'}",
            tuple(params + ["approved"]),
        )
        completed = row[0] if row else 0

        # Failed (new_state = 'failed')
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT task_id) FROM task_events"
            f"{' WHERE ' + ' AND '.join(where_parts + ['new_state = ?']) if where_parts else ' WHERE new_state = ?'}",
            tuple(params + ["failed"]),
        )
        failed = row[0] if row else 0

        # Tasks that had exec_failed (retried)
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT task_id) FROM task_events"
            f"{' WHERE ' + ' AND '.join(where_parts + ['new_state = ?']) if where_parts else ' WHERE new_state = ?'}",
            tuple(params + ["exec_failed"]),
        )
        retried = row[0] if row else 0

        # Events by type
        rows = await self._db.fetchall(
            f"SELECT event_type, COUNT(*) FROM task_events{where_clause} GROUP BY event_type",
            tuple(params),
        )
        events_by_type = {r[0]: r[1] for r in rows}

        # Active sessions
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT session_key) FROM task_events{where_clause}",
            tuple(params),
        )
        sessions_active = row[0] if row else 0

        retry_rate = retried / total_tasks if total_tasks > 0 else 0.0

        return {
            "total_tasks": total_tasks,
            "completed": completed,
            "failed": failed,
            "retried": retried,
            "retry_rate": round(retry_rate, 3),
            "events_by_type": events_by_type,
            "sessions_active": sessions_active,
            "time_range": {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            },
        }

    async def daily(self, date: datetime | None = None) -> dict[str, Any]:
        """Collect metrics for a single day."""
        day = date or datetime.now()
        since = day.replace(hour=0, minute=0, second=0, microsecond=0)
        until = since + timedelta(days=1)
        return await self.collect(since=since, until=until)

    async def weekly(self, end_date: datetime | None = None) -> dict[str, Any]:
        """Collect metrics for the past 7 days."""
        end = end_date or datetime.now()
        until = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        since = until - timedelta(days=7)
        return await self.collect(since=since, until=until)
