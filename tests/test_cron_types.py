"""Tests for Pydantic cron types round-trip serialization."""

from src.cron.types import CronJob, CronSchedule, CronStore


def test_serialize_roundtrip() -> None:
    store = CronStore(
        jobs=[
            CronJob(id="abc", name="test job", schedule=CronSchedule(kind="every", every_ms=5000))
        ]
    )
    data = store.model_dump(by_alias=True)
    assert data["jobs"][0]["schedule"]["everyMs"] == 5000
    restored = CronStore.model_validate(data)
    assert restored.jobs[0].schedule.every_ms == 5000
    assert restored.jobs[0].name == "test job"


def test_camel_case_aliases() -> None:
    job = CronJob(id="x", name="test", created_at_ms=1000, delete_after_run=True)
    d = job.model_dump(by_alias=True)
    assert "createdAtMs" in d
    assert "deleteAfterRun" in d
