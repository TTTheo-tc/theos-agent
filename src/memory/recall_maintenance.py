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
import os
import tempfile
from pathlib import Path
from typing import Any

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

    # Load existing targets (or start fresh)
    targets: dict[str, dict[str, Any]] = {}
    if targets_path.exists():
        try:
            targets = json.loads(targets_path.read_text())
        except (json.JSONDecodeError, OSError):
            targets = {}

    # Load checkpoint
    offset = 0
    if checkpoint_path.exists() and targets:
        try:
            cp = json.loads(checkpoint_path.read_text())
            offset = cp.get("byte_offset", 0)
        except (json.JSONDecodeError, OSError):
            offset = 0
            targets = {}  # checkpoint invalid → full rebuild

    # Read journal from offset
    updated_ids: set[str] = set()
    try:
        with open(journal_path, "r") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                tid = entry.get("target_id")
                kind = entry.get("target_kind", "")
                if not tid or kind not in ("kg_rule", "rule"):
                    continue

                if tid not in targets:
                    targets[tid] = {
                        "recall_count": 0,
                        "distinct_query_hashes": [],
                        "distinct_days": [],
                        "last_recalled_at": "",
                        "max_score": 0.0,
                        "total_score": 0.0,
                        "daily_count": 0,
                        "daily_counts": {},
                    }

                t = targets[tid]
                # Backfill v2.1 fields for targets written by older schema.
                t.setdefault("total_score", 0.0)
                t.setdefault("daily_count", 0)
                t.setdefault("daily_counts", {})

                t["recall_count"] += 1
                t["last_recalled_at"] = entry.get("timestamp", t["last_recalled_at"])

                qh = entry.get("query_hash", "")
                if qh and qh not in t["distinct_query_hashes"]:
                    if len(t["distinct_query_hashes"]) < _MAX_QUERY_HASHES:
                        t["distinct_query_hashes"].append(qh)

                day = entry.get("day", "")
                if day and day not in t["distinct_days"]:
                    if len(t["distinct_days"]) < _MAX_DAYS:
                        t["distinct_days"].append(day)

                score = entry.get("score")
                if score is not None and score > t["max_score"]:
                    t["max_score"] = score

                # v2.1: cumulative score + per-day consolidation signal.
                t["total_score"] += score or 0
                if day:
                    t["daily_counts"][day] = t["daily_counts"].get(day, 0) + 1
                    t["daily_count"] = max(t["daily_count"], t["daily_counts"][day])

                updated_ids.add(tid)

            new_offset = f.tell()
    except OSError:
        logger.opt(exception=True).warning("Failed to read recall journal")
        return 0

    # Write targets + checkpoint atomically (tmp + rename)
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

    from src.memory.structured import StructuredMemoryStore

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

            meta = existing.get("metadata") or "{}"
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}

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
