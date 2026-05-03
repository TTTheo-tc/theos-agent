"""Tests for reporting module — metrics collection and report generation."""

from datetime import datetime
from pathlib import Path

import pytest

from src.reporting.generator import ReportGenerator
from src.reporting.metrics import MetricsCollector
from src.store.database import Database
from src.store.event_store import EventStore


@pytest.fixture
async def db_with_events(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    es = EventStore(db)

    # Simulate task lifecycle events
    await es.append(
        "t1",
        "sess1",
        {
            "type": "created",
            "new_state": "pending",
            "timestamp": "2025-06-01T10:00:00",
        },
    )
    await es.append(
        "t1",
        "sess1",
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-06-01T10:00:01",
        },
    )
    await es.append(
        "t1",
        "sess1",
        {
            "type": "transition",
            "old_state": "executing",
            "new_state": "approved",
            "timestamp": "2025-06-01T10:00:05",
        },
    )

    # A failed task
    await es.append(
        "t2",
        "sess1",
        {
            "type": "created",
            "new_state": "pending",
            "timestamp": "2025-06-01T11:00:00",
        },
    )
    await es.append(
        "t2",
        "sess1",
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-06-01T11:00:01",
        },
    )
    await es.append(
        "t2",
        "sess1",
        {
            "type": "transition",
            "old_state": "executing",
            "new_state": "exec_failed",
            "timestamp": "2025-06-01T11:00:02",
        },
    )
    await es.append(
        "t2",
        "sess1",
        {
            "type": "transition",
            "old_state": "exec_failed",
            "new_state": "failed",
            "timestamp": "2025-06-01T11:00:03",
        },
    )

    # Another session
    await es.append(
        "t3",
        "sess2",
        {
            "type": "created",
            "new_state": "pending",
            "timestamp": "2025-06-01T12:00:00",
        },
    )
    await es.append(
        "t3",
        "sess2",
        {
            "type": "transition",
            "old_state": "pending",
            "new_state": "executing",
            "timestamp": "2025-06-01T12:00:01",
        },
    )
    await es.append(
        "t3",
        "sess2",
        {
            "type": "transition",
            "old_state": "executing",
            "new_state": "approved",
            "timestamp": "2025-06-01T12:00:05",
        },
    )

    yield db
    await db.close()


async def test_collect_all(db_with_events: Database):
    collector = MetricsCollector(db_with_events)
    m = await collector.collect()
    assert m["total_tasks"] == 3
    assert m["completed"] == 2
    assert m["failed"] == 1
    assert m["retried"] == 1
    assert m["sessions_active"] == 2
    assert m["retry_rate"] > 0


async def test_collect_with_time_filter(db_with_events: Database):
    collector = MetricsCollector(db_with_events)
    since = datetime(2025, 6, 1, 11, 30, 0)
    m = await collector.collect(since=since)
    # Only t3 (sess2 at 12:00) should match
    assert m["total_tasks"] == 1
    assert m["completed"] == 1
    assert m["failed"] == 0


async def test_daily(db_with_events: Database):
    collector = MetricsCollector(db_with_events)
    m = await collector.daily(date=datetime(2025, 6, 1))
    assert m["total_tasks"] == 3


async def test_weekly(db_with_events: Database):
    collector = MetricsCollector(db_with_events)
    m = await collector.weekly(end_date=datetime(2025, 6, 2))
    assert m["total_tasks"] == 3


def test_render_report():
    metrics = {
        "total_tasks": 10,
        "completed": 8,
        "failed": 2,
        "retried": 1,
        "retry_rate": 0.1,
        "sessions_active": 3,
        "events_by_type": {"created": 10, "transition": 20},
        "time_range": {"since": "2025-06-01T00:00:00", "until": "2025-06-02T00:00:00"},
    }
    report = ReportGenerator.render(metrics, title="Test Report")
    assert "# Test Report" in report
    assert "| Total tasks | 10 |" in report
    assert "| Completed | 8 |" in report
    assert "| Failed | 2 |" in report
    assert "80.0%" in report
    assert "Events by Type" in report
    assert "| transition | 20 |" in report


def test_render_empty_report():
    metrics = {
        "total_tasks": 0,
        "completed": 0,
        "failed": 0,
        "retried": 0,
        "retry_rate": 0,
        "sessions_active": 0,
        "events_by_type": {},
        "time_range": {"since": None, "until": None},
    }
    report = ReportGenerator.render(metrics)
    assert "# TheOS Report" in report
    assert "| Total tasks | 0 |" in report
