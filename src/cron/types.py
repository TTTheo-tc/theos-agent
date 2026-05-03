"""Cron types using Pydantic for serialization."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CronBase(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CronSchedule(CronBase):
    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


class CronPayload(CronBase):
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    deliver: bool = False
    channel: str | None = None
    to: str | None = None


class CronJobState(CronBase):
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None


class CronJob(CronBase):
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = Field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = Field(default_factory=CronPayload)
    state: CronJobState = Field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    expires_at_ms: int | None = None
    delete_after_run: bool = False


class CronStore(CronBase):
    version: int = 1
    jobs: list[CronJob] = Field(default_factory=list)
