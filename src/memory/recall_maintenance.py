"""Recall maintenance — offline fold + KG ingestion.

Runs during the 6h maintenance window. Two steps:
1. fold_recall_journal(): consume journal tail → update recall_targets.json
2. ingest_recall_to_kg(): read targets snapshot → update KG rule metadata

recall_targets.json is a derived cache. If missing, fold rebuilds from
the full journal (fallback full rebuild).
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from loguru import logger

from src.memory.memory_events import append_memory_event

_INSTINCT_DIR = Path("memory") / "instinct"
_JOURNAL_REL = _INSTINCT_DIR / "recall_journal.jsonl"
_TARGETS_REL = _INSTINCT_DIR / "recall_targets.json"
_CHECKPOINT_REL = _INSTINCT_DIR / "recall_targets.checkpoint.json"

_MAX_QUERY_HASHES = 32
_MAX_DAYS = 16


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via tmp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json_object(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}, False
    return (data, True) if isinstance(data, dict) else ({}, False)


def _checkpoint_offset(path: Path, targets: dict[str, dict[str, Any]]) -> tuple[int, bool]:
    if not path.exists() or not targets:
        return 0, False

    checkpoint, valid_json = _read_json_object(path)
    if not valid_json:
        return 0, False
    if "byte_offset" not in checkpoint:
        return 0, False
    offset = checkpoint["byte_offset"]
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return 0, False
    return offset, True


def _iter_jsonl(handle: TextIO) -> Iterator[dict[str, Any]]:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def _empty_recall_target() -> dict[str, Any]:
    return {
        "recall_count": 0,
        "distinct_query_hashes": [],
        "distinct_days": [],
        "last_recalled_at": "",
        "max_score": 0.0,
        "total_score": 0.0,
        "daily_count": 0,
        "daily_counts": {},
    }


def _target_for(targets: dict[str, dict[str, Any]], target_id: str) -> dict[str, Any]:
    target = targets.setdefault(target_id, _empty_recall_target())
    target.setdefault("total_score", 0.0)
    target.setdefault("daily_count", 0)
    target.setdefault("daily_counts", {})
    return target


def _score_value(entry: dict[str, Any]) -> float | None:
    score = entry.get("score")
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _append_capped_unique(values: list[str], value: str, limit: int) -> None:
    if value and value not in values and len(values) < limit:
        values.append(value)


def _fold_recall_entry(
    targets: dict[str, dict[str, Any]],
    entry: dict[str, Any],
) -> str | None:
    target_id = entry.get("target_id")
    kind = entry.get("target_kind", "")
    if not target_id or kind not in ("kg_rule", "rule"):
        return None

    target_id = str(target_id)
    target = _target_for(targets, target_id)
    target["recall_count"] += 1
    target["last_recalled_at"] = entry.get("timestamp", target["last_recalled_at"])

    _append_capped_unique(
        target["distinct_query_hashes"],
        str(entry.get("query_hash") or ""),
        _MAX_QUERY_HASHES,
    )

    day = str(entry.get("day") or "")
    _append_capped_unique(target["distinct_days"], day, _MAX_DAYS)

    score = _score_value(entry)
    if score is not None and score > target["max_score"]:
        target["max_score"] = score
    target["total_score"] += score or 0

    if day:
        target["daily_counts"][day] = target["daily_counts"].get(day, 0) + 1
        target["daily_count"] = max(target["daily_count"], target["daily_counts"][day])

    return target_id


def fold_recall_journal(workspace: Path) -> int:
    """Fold recall_journal.jsonl tail into recall_targets.json.

    Uses a byte-offset checkpoint for incremental processing.
    Falls back to full rebuild if checkpoint is missing or invalid.

    Returns the number of distinct KG targets updated, or 0 if skipped
    (e.g. no journal, or lock held by another process).
    """
    journal_path = workspace / _JOURNAL_REL
    if not journal_path.exists():
        return 0

    # Single-instance lock
    lock_path = journal_path.parent / "recall_maintenance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        logger.debug("Recall maintenance lock held by another process, skipping")
        return 0

    try:
        return _fold_recall_journal_locked(workspace)
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def _fold_recall_journal_locked(workspace: Path) -> int:
    """Inner fold logic, must be called while holding the maintenance lock."""
    journal_path = workspace / _JOURNAL_REL
    targets_path = workspace / _TARGETS_REL
    checkpoint_path = workspace / _CHECKPOINT_REL

    targets, _targets_valid = _read_json_object(targets_path)
    offset, checkpoint_valid = _checkpoint_offset(checkpoint_path, targets)
    if targets and not checkpoint_valid:
        targets = {}

    updated_ids: set[str] = set()
    try:
        with open(journal_path, "r") as f:
            f.seek(offset)
            for entry in _iter_jsonl(f):
                target_id = _fold_recall_entry(targets, entry)
                if target_id:
                    updated_ids.add(target_id)

            new_offset = f.tell()
    except (OSError, ValueError):
        logger.opt(exception=True).warning("Failed to read recall journal")
        return 0

    try:
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(targets_path, json.dumps(targets, indent=2, ensure_ascii=False) + "\n")
        _atomic_write(checkpoint_path, json.dumps({"byte_offset": new_offset}) + "\n")
    except OSError:
        logger.opt(exception=True).warning("Failed to write recall targets")
        return len(updated_ids)

    append_memory_event(
        workspace=workspace,
        event_type="memory.recall.folded",
        payload={"targets_updated": len(updated_ids)},
    )
    return len(updated_ids)


async def ingest_recall_to_kg(workspace: Path) -> int:
    """Batch-update KG rule nodes with recall metadata from targets snapshot."""
    targets_path = workspace / _TARGETS_REL
    if not targets_path.exists():
        return 0

    try:
        targets = json.loads(targets_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    from src.memory.structured import StructuredMemoryStore, _coerce_metadata

    updated = 0
    store = StructuredMemoryStore(workspace)
    try:
        await store.ensure_kg()
        if store._kg is None:
            return 0

        for target_id, data in targets.items():
            if not target_id.startswith("rule-"):
                continue
            existing = await store._kg.get_node(target_id)
            if existing is None:
                continue

            meta = _coerce_metadata(existing.get("metadata"))
            meta["recall_count"] = data.get("recall_count", 0)
            meta["last_recalled_at"] = data.get("last_recalled_at", "")
            meta["distinct_recall_queries"] = len(data.get("distinct_query_hashes", []))
            meta["distinct_recall_days"] = len(data.get("distinct_days", []))

            await store._kg.update_node(target_id, metadata=meta)
            updated += 1
    finally:
        await store.close()

    if updated:
        logger.info("Recall ingestion: updated {} KG rule(s)", updated)
    append_memory_event(
        workspace=workspace,
        event_type="memory.recall.ingested",
        payload={"rules_updated": updated},
    )
    return updated
