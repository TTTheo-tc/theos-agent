"""Cleanup helpers for structured rule records."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from src.cron.service import CronService
from src.cron.types import CronSchedule
from src.memory.structured_models import is_noise_response, is_transferable_rule_text
from src.utils.helpers import ensure_dir

STRUCTURED_RULE_CLEANUP_JOB_NAME = "structured-rule-cleanup"
STRUCTURED_RULE_CLEANUP_EVENT = "structured_rule_cleanup"
STRUCTURED_RULE_CLEANUP_INTERVAL_MS = 6 * 60 * 60 * 1000

_PROMPT_NOISE_MARKERS = (
    "推荐 Skills（按需 read_file 加载 SKILL",
    "[Ephemeral Context",
    "[Runtime Context",
    "[Structured Recall]",
    "Received Messages=[",
)


@dataclass
class StructuredRuleCleanupReport:
    scanned: int = 0
    kept: int = 0
    quarantined: int = 0
    quarantined_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.scanned == 0:
            return "Structured rule cleanup: no rule files found."
        return (
            "Structured rule cleanup: "
            f"scanned={self.scanned}, kept={self.kept}, quarantined={self.quarantined}"
        )


def cleanup_structured_rules(workspace: Path) -> StructuredRuleCleanupReport:
    """Quarantine obviously dirty structured rule records."""
    base_dir = workspace / "memory" / "structured"
    rules_dir = base_dir / "rules"
    quarantine_dir = ensure_dir(base_dir / "rules_quarantine")
    report = StructuredRuleCleanupReport()

    if not rules_dir.exists():
        return report

    task_statuses = _load_task_statuses(base_dir / "tasks")
    for rule_path in sorted(rules_dir.glob("*.json")):
        report.scanned += 1
        reason = _dirty_rule_reason(rule_path, task_statuses)
        if reason is None:
            report.kept += 1
            continue
        target = quarantine_dir / rule_path.name
        if target.exists():
            target = quarantine_dir / f"{rule_path.stem}-{reason}{rule_path.suffix}"
        shutil.move(str(rule_path), str(target))
        report.quarantined += 1
        report.quarantined_files.append(target.name)

    return report


def ensure_structured_rule_cleanup_job(service: CronService) -> bool:
    """Ensure the recurring 6-hour structured rule cleanup job exists."""
    desired_schedule = CronSchedule(kind="every", every_ms=STRUCTURED_RULE_CLEANUP_INTERVAL_MS)
    jobs = [
        job
        for job in service.list_jobs(include_disabled=True)
        if job.name == STRUCTURED_RULE_CLEANUP_JOB_NAME
    ]

    matching = [
        job
        for job in jobs
        if job.payload.kind == "system_event"
        and job.payload.message == STRUCTURED_RULE_CLEANUP_EVENT
        and job.schedule.kind == desired_schedule.kind
        and job.schedule.every_ms == desired_schedule.every_ms
    ]

    changed = False
    keep_id = matching[0].id if matching else None
    for job in jobs:
        if job.id == keep_id:
            continue
        service.remove_job(job.id)
        changed = True

    if matching:
        job = matching[0]
        if not job.enabled:
            service.enable_job(job.id, enabled=True)
            changed = True
        return changed

    service.add_job(
        name=STRUCTURED_RULE_CLEANUP_JOB_NAME,
        schedule=desired_schedule,
        message=STRUCTURED_RULE_CLEANUP_EVENT,
        kind="system_event",
    )
    return True


def run_structured_rule_cleanup_event(workspace: Path, event_name: str) -> str:
    """Execute a supported structured-rule system event."""
    if event_name != STRUCTURED_RULE_CLEANUP_EVENT:
        raise ValueError(f"unsupported system event '{event_name}'")
    cleanup_summary = cleanup_structured_rules(workspace).summary()

    # Recall maintenance: fold journal + ingest to KG
    import asyncio

    from src.memory.recall_maintenance import fold_recall_journal, ingest_recall_to_kg

    folded = fold_recall_journal(workspace)
    kg_updated = asyncio.run(ingest_recall_to_kg(workspace))
    recall_summary = (
        f"Recall maintenance: folded {folded} target(s), ingested {kg_updated} KG rule(s)"
    )

    # KG GC: remove long-superseded nodes
    try:
        from src.memory.kg_gc import gc_superseded_nodes

        gc_deleted = asyncio.run(gc_superseded_nodes(workspace))
        recall_summary += f"; KG GC deleted {gc_deleted} superseded node(s)"
    except Exception as exc:
        logger.opt(exception=True).warning("KG GC failed: {}", exc)

    return f"{cleanup_summary}\n{recall_summary}"


def _load_task_statuses(tasks_dir: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    if not tasks_dir.exists():
        return statuses
    for task_path in tasks_dir.glob("*.json"):
        try:
            payload = json.loads(task_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        task_id = str(payload.get("id") or "").strip()
        if task_id:
            statuses[task_id] = str(payload.get("status") or "")
    return statuses


def _dirty_rule_reason(rule_path: Path, task_statuses: dict[str, str]) -> str | None:
    try:
        payload = json.loads(rule_path.read_text(encoding="utf-8"))
    except Exception:
        return "invalid-json"

    rule_text = payload.get("rule_text")
    if not isinstance(rule_text, str) or not rule_text.strip():
        return "empty-rule"
    if is_noise_response(rule_text):
        return "noise-text"
    if not is_transferable_rule_text(rule_text):
        return "non-transferable"
    if any(marker in rule_text for marker in _PROMPT_NOISE_MARKERS):
        return "prompt-noise"
    if "|------|" in rule_text and "http" in rule_text:
        return "table-blob"

    source_task_ids = [str(item).strip() for item in payload.get("source_task_ids", []) if item]
    if source_task_ids:
        statuses = [task_statuses.get(task_id) for task_id in source_task_ids]
        if statuses and all(status and status != "success" for status in statuses):
            return "failed-source-task"

    return None
