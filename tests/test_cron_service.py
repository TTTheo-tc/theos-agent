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
