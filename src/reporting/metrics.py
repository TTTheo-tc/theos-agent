"""Metrics collector — reads task_events and computes aggregate statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.store.database import Database


def _where_clause(where_parts: list[str]) -> str:
    return (" WHERE " + " AND ".join(where_parts)) if where_parts else ""


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

        # Total unique tasks
        total_tasks = await self._count_distinct_tasks(where_parts, params)

        # Completed (new_state = 'approved')
        completed = await self._count_tasks_in_state("approved", where_parts, params)

        # Failed (new_state = 'failed')
        failed = await self._count_tasks_in_state("failed", where_parts, params)

        # Tasks that had exec_failed (retried)
        retried = await self._count_tasks_in_state("exec_failed", where_parts, params)

        # Events by type
        rows = await self._db.fetchall(
            f"SELECT event_type, COUNT(*) FROM task_events{_where_clause(where_parts)} GROUP BY event_type",
            tuple(params),
        )
        events_by_type = {r[0]: r[1] for r in rows}

        # Active sessions
        sessions_active = await self._count_distinct_sessions(where_parts, params)

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

    async def _count_distinct_tasks(self, where_parts: list[str], params: list[Any]) -> int:
        return await self._count_distinct("task_id", where_parts, params)

    async def _count_tasks_in_state(
        self,
        state: str,
        where_parts: list[str],
        params: list[Any],
    ) -> int:
        return await self._count_distinct_tasks(
            [*where_parts, "new_state = ?"],
            [*params, state],
        )

    async def _count_distinct_sessions(self, where_parts: list[str], params: list[Any]) -> int:
        return await self._count_distinct("session_key", where_parts, params)

    async def _count_distinct(
        self,
        column: str,
        where_parts: list[str],
        params: list[Any],
    ) -> int:
        row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT {column}) FROM task_events{_where_clause(where_parts)}",
            tuple(params),
        )
        return row[0] if row else 0

    async def weekly(self, end_date: datetime | None = None) -> dict[str, Any]:
        """Collect metrics for the past 7 days."""
        end = end_date or datetime.now()
        until = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        since = until - timedelta(days=7)
        return await self.collect(since=since, until=until)
