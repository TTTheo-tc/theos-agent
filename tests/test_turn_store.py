"""Tests for durable turn checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.session.turn_store import TurnStore


def test_turn_store_records_and_reads_latest(tmp_path: Path):
    store = TurnStore(tmp_path)

    store.record("cli:direct", "turn-1", "accepted", channel="cli")
    store.record("cli:direct", "turn-1", "inferring")

    latest = store.latest("cli:direct")
    assert latest is not None
    assert latest.turn_id == "turn-1"
    assert latest.status == "inferring"


def test_turn_store_latest_missing_session_returns_none(tmp_path: Path):
    store = TurnStore(tmp_path)

    assert store.latest("cli:missing") is None


def test_turn_store_marks_inflight_turns_interrupted(tmp_path: Path):
    store = TurnStore(tmp_path)

    store.record("cli:direct", "turn-1", "waiting_user", question="Need more info")
    store.record("telegram:1", "turn-2", "completed")

    marked = store.mark_interrupted_inflight(reason="gateway restart")

    assert marked == 1
    latest = store.latest("cli:direct")
    assert latest is not None
    assert latest.status == "interrupted"
    assert latest.metadata["interrupted_from"] == "waiting_user"
    assert latest.metadata["question"] == "Need more info"


def test_turn_store_converts_metadata_to_json_safe_values(tmp_path: Path):
    store = TurnStore(tmp_path)
    when = datetime(2026, 1, 2, tzinfo=timezone.utc)

    checkpoint = store.record(
        "cli:direct",
        "turn-1",
        "accepted",
        path=tmp_path / "artifact.txt",
        when=when,
        skip=None,
    )

    assert checkpoint.metadata == {
        "path": str(tmp_path / "artifact.txt"),
        "when": when.isoformat(),
    }
