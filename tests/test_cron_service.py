import importlib.util

import pytest

from src.cron.service import CronService, build_schedule
from src.cron.types import CronSchedule


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None


def test_add_job_reports_missing_croniter(monkeypatch, tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "croniter":
            return None
        return original_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(ValueError, match="cron schedules require croniter"):
        service.add_job(
            name="missing extra",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_rejects_non_runnable_every_schedule(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="every schedule requires every_ms > 0"):
        service.add_job(
            name="bad interval",
            schedule=CronSchedule(kind="every", every_ms=0),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_rejects_non_runnable_at_schedule(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="at schedule must be in the future"):
        service.add_job(
            name="past one-shot",
            schedule=CronSchedule(kind="at", at_ms=1),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_remove_last_job_persists_empty_store(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    job = service.add_job(
        name="remove me",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    assert service.remove_job(job.id) is True

    restored = CronService(store_path)
    assert restored.list_jobs(include_disabled=True) == []


def test_enable_job_updates_persisted_schedule_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    job = service.add_job(
        name="toggle me",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    disabled = service.enable_job(job.id, enabled=False)

    assert disabled is not None
    assert disabled.enabled is False
    assert disabled.state.next_run_at_ms is None

    restored = CronService(store_path)
    restored_job = restored.list_jobs(include_disabled=True)[0]
    assert restored_job.enabled is False
    assert restored_job.state.next_run_at_ms is None


async def test_run_job_updates_persisted_state(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    calls: list[str] = []

    async def on_job(job):
        calls.append(job.id)
        return "ok"

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="run me",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    assert await service.run_job(job.id) is True
    assert calls == [job.id]

    restored = CronService(store_path)
    restored_job = restored.list_jobs(include_disabled=True)[0]
    assert restored_job.state.last_status == "ok"
    assert restored_job.state.last_run_at_ms is not None


async def test_remove_last_job_clears_running_timer(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    job = service.add_job(
        name="remove me",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="hello",
    )

    await service.start()
    assert service._timer_task is not None

    assert service.remove_job(job.id) is True
    assert service._timer_task is None

    service.stop()


def test_build_schedule_one_shot_requests_delete_after_run() -> None:
    schedule, delete_after = build_schedule(at="2026-01-02T03:04:05")

    assert schedule.kind == "at"
    assert schedule.at_ms is not None
    assert delete_after is True


def test_build_schedule_rejects_tz_without_cron() -> None:
    with pytest.raises(ValueError, match="tz can only be used with cron schedules"):
        build_schedule(every_seconds=60, tz="UTC")


def test_build_schedule_preserves_zero_interval_for_service_validation() -> None:
    schedule, delete_after = build_schedule(every_seconds=0)

    assert schedule.kind == "every"
    assert schedule.every_ms == 0
    assert delete_after is False


async def test_cron_tool_preserves_tz_without_cron_error_text(tmp_path) -> None:
    from src.agent.tools.context import ToolContext
    from src.agent.tools.cron import CronTool

    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)

    result = await tool.execute(
        action="add",
        message="hello",
        every_seconds=60,
        tz="UTC",
        _context=ToolContext(channel="cli", chat_id="direct"),
    )

    assert result == "Error: tz can only be used with cron_expr"


async def test_cron_tool_returns_error_for_negative_interval(tmp_path) -> None:
    from src.agent.tools.context import ToolContext
    from src.agent.tools.cron import CronTool

    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)

    result = await tool.execute(
        action="add",
        message="hello",
        every_seconds=-1,
        _context=ToolContext(channel="cli", chat_id="direct"),
    )

    assert result == "Error: every schedule requires every_ms > 0"
    assert service.list_jobs(include_disabled=True) == []


async def test_cron_tool_returns_error_for_past_one_shot(tmp_path) -> None:
    from src.agent.tools.context import ToolContext
    from src.agent.tools.cron import CronTool

    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)

    result = await tool.execute(
        action="add",
        message="hello",
        at="2000-01-01T00:00:00",
        _context=ToolContext(channel="cli", chat_id="direct"),
    )

    assert result == "Error: at schedule must be in the future"
    assert service.list_jobs(include_disabled=True) == []
