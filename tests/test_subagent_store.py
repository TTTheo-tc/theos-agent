"""Tests for durable subagent/background checkpoints."""

from __future__ import annotations

from pathlib import Path

from src.session.subagent_store import SubagentStore


def test_subagent_store_tracks_latest_and_active(tmp_path: Path):
    store = SubagentStore(tmp_path)

    store.record("cli:direct", "sub-1", "pending", label="explore")
    store.record("cli:direct", "sub-1", "running", label="explore")
    store.record("cli:direct", "sub-2", "completed", label="done")

    active = store.active_for_session("cli:direct")
    latest = store.latest_for_session("cli:direct")

    assert len(active) == 1
    assert active[0].task_id == "sub-1"
    assert active[0].status == "running"
    assert {cp.task_id for cp in latest} == {"sub-1", "sub-2"}


def test_subagent_store_marks_inflight_tasks_interrupted(tmp_path: Path):
    store = SubagentStore(tmp_path)

    store.record("cli:direct", "sub-1", "running", label="explore", task="scan repo")
    store.record("cli:direct", "sub-2", "completed", label="done")

    marked = store.mark_interrupted_inflight(reason="gateway restart")

    assert marked == 1
    latest = store.latest_for_session("cli:direct")
    interrupted = next(cp for cp in latest if cp.task_id == "sub-1")
    assert interrupted.status == "interrupted"
    assert interrupted.metadata["interrupted_from"] == "running"
    assert interrupted.metadata["task"] == "scan repo"


def test_subagent_store_keeps_rows_read_before_corrupt_line(tmp_path: Path):
    store = SubagentStore(tmp_path)
    path = store._get_path("cli:direct")
    path.write_text(
        "\n".join(
            [
                (
                    '{"_type":"subagent_checkpoint","session_key":"cli:direct",'
                    '"task_id":"sub-1","status":"running","timestamp":"2026-01-01T00:00:00Z"}'
                ),
                "not json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    active = store.active_for_session("cli:direct")

    assert len(active) == 1
    assert active[0].task_id == "sub-1"
    assert active[0].status == "running"
