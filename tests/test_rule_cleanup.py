from __future__ import annotations

import json
from pathlib import Path

from src.cron.service import CronService
from src.memory.rule_cleanup import (
    STRUCTURED_RULE_CLEANUP_EVENT,
    STRUCTURED_RULE_CLEANUP_INTERVAL_MS,
    STRUCTURED_RULE_CLEANUP_JOB_NAME,
    cleanup_structured_rules,
    ensure_structured_rule_cleanup_job,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_cleanup_structured_rules_quarantines_failed_source_noise(tmp_path: Path) -> None:
    base = tmp_path / "memory" / "structured"
    _write_json(
        base / "tasks" / "task-bad.json",
        {"id": "task-bad", "status": "failed"},
    )
    _write_json(
        base / "tasks" / "task-good.json",
        {"id": "task-good", "status": "success"},
    )
    _write_json(
        base / "rules" / "rule-bad.json",
        {
            "id": "rule-bad",
            "rule_text": "记住了\\\\'}, {\\\\'role\\\\': \\\\'assistant\\\\', \\\\'content\\\\': \\\\'明白，记住了。",
            "source_task_ids": ["task-bad"],
        },
    )
    _write_json(
        base / "rules" / "rule-good.json",
        {
            "id": "rule-good",
            "rule_text": "以后验证结构时，逐层 feishu_list 展开确认。",
            "source_task_ids": ["task-good"],
        },
    )

    report = cleanup_structured_rules(tmp_path)

    assert report.scanned == 2
    assert report.kept == 1
    assert report.quarantined == 1
    assert not (base / "rules" / "rule-bad.json").exists()
    assert (base / "rules_quarantine" / "rule-bad.json").exists()
    assert (base / "rules" / "rule-good.json").exists()


def test_cleanup_structured_rules_handles_numeric_source_task_ids(tmp_path: Path) -> None:
    base = tmp_path / "memory" / "structured"
    _write_json(
        base / "tasks" / "task-1.json",
        {"id": 1, "status": "failed"},
    )
    _write_json(
        base / "rules" / "rule-bad.json",
        {
            "id": "rule-bad",
            "rule_text": "以后验证结构时，逐层 feishu_list 展开确认。",
            "source_task_ids": [1, None, ""],
        },
    )

    report = cleanup_structured_rules(tmp_path)

    assert report.scanned == 1
    assert report.quarantined == 1
    assert (base / "rules_quarantine" / "rule-bad.json").exists()


def test_cleanup_structured_rules_quarantines_context_specific_text(tmp_path: Path) -> None:
    base = tmp_path / "memory" / "structured"
    _write_json(
        base / "tasks" / "task-good.json",
        {"id": "task-good", "status": "success"},
    )
    _write_json(
        base / "rules" / "rule-bad.json",
        {
            "id": "rule-bad",
            "rule_text": "注意到这个 wiki 下面还有 4 个测试文档，你之前让我删的应该就是这些。",
            "source_task_ids": ["task-good"],
        },
    )

    report = cleanup_structured_rules(tmp_path)

    assert report.scanned == 1
    assert report.kept == 0
    assert report.quarantined == 1
    assert not (base / "rules" / "rule-bad.json").exists()
    assert (base / "rules_quarantine" / "rule-bad.json").exists()


def test_cleanup_structured_rules_is_idempotent(tmp_path: Path) -> None:
    base = tmp_path / "memory" / "structured"
    _write_json(
        base / "tasks" / "task-bad.json",
        {"id": "task-bad", "status": "failed"},
    )
    _write_json(
        base / "rules" / "rule-bad.json",
        {
            "id": "rule-bad",
            "rule_text": "Received Messages=[{'role': 'user'}]",
            "source_task_ids": ["task-bad"],
        },
    )

    first = cleanup_structured_rules(tmp_path)
    second = cleanup_structured_rules(tmp_path)

    assert first.quarantined == 1
    assert second.scanned == 0
    assert second.quarantined == 0


def test_ensure_structured_rule_cleanup_job_is_created_and_deduped(tmp_path: Path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    changed = ensure_structured_rule_cleanup_job(service)
    assert changed is True

    jobs = service.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == STRUCTURED_RULE_CLEANUP_JOB_NAME
    assert job.payload.kind == "system_event"
    assert job.payload.message == STRUCTURED_RULE_CLEANUP_EVENT
    assert job.schedule.kind == "every"
    assert job.schedule.every_ms == STRUCTURED_RULE_CLEANUP_INTERVAL_MS

    changed_again = ensure_structured_rule_cleanup_job(service)
    jobs_again = service.list_jobs(include_disabled=True)
    assert changed_again is False
    assert len(jobs_again) == 1
