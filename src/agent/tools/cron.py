"""Cron tool for scheduling reminders and tasks."""

from typing import Any

from src.agent.tools.base import ContextAwareTool
from src.cron.service import CronService, build_schedule

_MAX_JOBS = 50


class CronTool(ContextAwareTool):
    """Tool to schedule reminders and recurring tasks."""

    @property
    def owner_only(self) -> bool:
        return True

    def __init__(self, cron_service: CronService):
        self._cron = cron_service

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "message": {"type": "string", "description": "Reminder message (for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone for cron expressions (e.g. 'America/Vancouver')",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        _context: Any = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        from src.agent.tools.context import ToolContext

        ctx = _context or ToolContext()
        return self._dispatch(action, ctx, message, every_seconds, cron_expr, tz, at, job_id)

    def _dispatch(
        self,
        action: str,
        ctx: Any,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        job_id: str | None,
    ) -> str:
        if action == "add":
            return self._add_job(ctx, message, every_seconds, cron_expr, tz, at)
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        ctx: Any,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not ctx.channel or not ctx.chat_id:
            return "Error: no session context (channel/chat_id)"
        if len(self._cron.list_jobs()) >= _MAX_JOBS:
            return f"Error: maximum number of jobs ({_MAX_JOBS}) reached. Remove some before adding new ones."
        try:
            schedule, delete_after = build_schedule(
                every_seconds=every_seconds,
                cron_expr=cron_expr,
                at=at,
                tz=tz,
            )
        except ValueError as exc:
            if "tz can only be used" in str(exc):
                return "Error: tz can only be used with cron_expr"
            return f"Error: {exc}"

        try:
            job = self._cron.add_job(
                name=message[:30],
                schedule=schedule,
                message=message,
                deliver=True,
                channel=ctx.channel,
                to=ctx.chat_id,
                delete_after_run=delete_after,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        return f"Created job '{job.name}' (id: {job.id})"

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [f"- {j.name} (id: {j.id}, {j.schedule.kind})" for j in jobs]
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"
