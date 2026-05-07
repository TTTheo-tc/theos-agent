"""Cron service for scheduling agent tasks."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from src.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore

_RECURRING_JOB_TTL_MS = 30 * 24 * 60 * 60 * 1000

CronJobCallback = Callable[[CronJob], Awaitable[str | None]]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter

            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule, *, now_ms: int | None = None) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    reference_ms = _now_ms() if now_ms is None else now_ms

    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")
    if schedule.kind == "at":
        if not schedule.at_ms or schedule.at_ms <= reference_ms:
            raise ValueError("at schedule must be in the future")
        return
    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            raise ValueError("every schedule requires every_ms > 0")
        return

    _validate_cron_schedule(schedule, reference_ms)


def _validate_cron_schedule(schedule: CronSchedule, reference_ms: int) -> None:
    if schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None
    if not schedule.expr:
        raise ValueError("cron schedule requires expr")
    if importlib.util.find_spec("croniter") is None:
        raise ValueError("cron schedules require croniter; install the gateway extra")
    if _compute_next_run(schedule, reference_ms) is None:
        raise ValueError("cron schedule could not compute next run")


def build_schedule(
    *,
    every_seconds: int | None = None,
    cron_expr: str | None = None,
    at: str | None = None,
    tz: str | None = None,
) -> tuple[CronSchedule, bool]:
    """Build a schedule from public CLI/tool inputs.

    Returns ``(schedule, delete_after_run)``.
    """
    if tz and not cron_expr:
        raise ValueError("tz can only be used with cron schedules")
    if every_seconds is not None:
        return CronSchedule(kind="every", every_ms=every_seconds * 1000), False
    if cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        _validate_schedule_for_add(schedule)
        return schedule, False
    if at:
        dt = datetime.fromisoformat(at)
        return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000)), True
    raise ValueError("either every_seconds, cron_expr, or at is required")


class CronService:
    """Service for managing and executing scheduled jobs."""

    def __init__(
        self,
        store_path: Path,
        on_job: CronJobCallback | None = None,
    ) -> None:
        self.store_path = store_path
        self.on_job = on_job  # Callback to execute job, returns response text
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._running = False

    def _load_store(self) -> CronStore:
        """Load jobs from disk."""
        if self._store is not None:
            return self._store
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self._store = CronStore.model_validate(data)
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
                self._store = CronStore()
        else:
            self._store = CronStore()
        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if self._store is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._store.model_dump(by_alias=True)
        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _save_and_rearm(self) -> None:
        self._save_store()
        self._arm_timer()

    def _find_job(self, job_id: str) -> CronJob | None:
        store = self._load_store()
        return next((job for job in store.jobs if job.id == job_id), None)

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info(
            "Cron service started with {} jobs", len(self._store.jobs if self._store else [])
        )

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        """Cancel and clear the armed timer task."""
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs.

        One-shot ``at`` jobs whose scheduled time has already passed are
        silently removed (they can never fire again).
        """
        if not self._store:
            return
        now = _now_ms()
        expired_ids: list[str] = []
        for job in self._store.jobs:
            if not job.enabled:
                continue
            # Remove past-due one-shot jobs
            if (
                job.schedule.kind == "at"
                and job.schedule.at_ms
                and job.schedule.at_ms <= now
                and not job.state.last_run_at_ms
            ):
                logger.info("Cron: removing expired one-shot job '{}' ({})", job.name, job.id)
                expired_ids.append(job.id)
                continue
            # Remove auto-expired recurring jobs
            if job.expires_at_ms and job.expires_at_ms <= now:
                logger.info("Cron: auto-expiring recurring job '{}' ({})", job.name, job.id)
                expired_ids.append(job.id)
                continue
            job.state.next_run_at_ms = _compute_next_run(job.schedule, now)
        if expired_ids:
            self._store.jobs = [j for j in self._store.jobs if j.id not in expired_ids]

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [
            j.state.next_run_at_ms for j in self._store.jobs if j.enabled and j.state.next_run_at_ms
        ]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick."""
        self._cancel_timer()

        next_wake = self._get_next_wake_ms()
        if not next_wake or not self._running:
            return

        delay_ms = max(0, next_wake - _now_ms())
        delay_s = delay_ms / 1000

        async def tick() -> None:
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        if not self._store:
            return

        now = _now_ms()
        due_jobs = [
            j
            for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
        ]

        tasks = [asyncio.create_task(self._execute_job(job)) for job in due_jobs]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._save_store()
        self._arm_timer()

    _JOB_TIMEOUT_S = 300  # 5 minutes max per job

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            if self.on_job:
                await asyncio.wait_for(self.on_job(job), timeout=self._JOB_TIMEOUT_S)

            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)

        except asyncio.TimeoutError:
            job.state.last_status = "error"
            job.state.last_error = f"timed out after {self._JOB_TIMEOUT_S}s"
            logger.warning("Cron: job '{}' timed out after {}s", job.name, self._JOB_TIMEOUT_S)

        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.opt(exception=True).warning("Cron: job '{}' failed", job.name)

        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = _now_ms()

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run from the scheduled time (not wall clock) to avoid drift
            base_ms = start_ms if job.schedule.kind == "every" else _now_ms()
            job.state.next_run_at_ms = _compute_next_run(job.schedule, base_ms)

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float("inf"))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        kind: Literal["system_event", "agent_turn"] = "agent_turn",
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """Add a new job."""
        store = self._load_store()
        now = _now_ms()
        _validate_schedule_for_add(schedule, now_ms=now)

        # Recurring jobs auto-expire after 30 days
        expires_at = now + _RECURRING_JOB_TTL_MS if schedule.kind != "at" else None

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind=kind,
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            expires_at_ms=expires_at,
            delete_after_run=delete_after_run,
        )

        store.jobs.append(job)
        self._save_and_rearm()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            self._save_and_rearm()
            logger.info("Cron: removed job {}", job_id)

        return removed

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""
        job = self._find_job(job_id)
        if job is None:
            return None
        job.enabled = enabled
        now = _now_ms()
        job.updated_at_ms = now
        job.state.next_run_at_ms = _compute_next_run(job.schedule, now) if enabled else None
        self._save_and_rearm()
        return job

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        job = self._find_job(job_id)
        if job is None or (not force and not job.enabled):
            return False
        await self._execute_job(job)
        self._save_and_rearm()
        return True

    def status(self) -> dict[str, Any]:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
