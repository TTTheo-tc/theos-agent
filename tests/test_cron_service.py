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


def test_build_schedule_one_shot_requests_delete_after_run() -> None:
    schedule, delete_after = build_schedule(at="2026-01-02T03:04:05")

    assert schedule.kind == "at"
    assert schedule.at_ms is not None
    assert delete_after is True


def test_build_schedule_rejects_tz_without_cron() -> None:
    with pytest.raises(ValueError, match="tz can only be used with cron schedules"):
        build_schedule(every_seconds=60, tz="UTC")


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
