"""Tests for recall journal writer."""

from __future__ import annotations

import json

import pytest


def test_entry_for_result_hashes_content_claim() -> None:
    from src.memory.recall_journal import _entry_for_result

    entry = _entry_for_result(
        {
            "target_kind": "markdown_section",
            "target_id": None,
            "path": "MEMORY:Decisions",
            "score": 0.8,
            "domains": ["coding"],
            "content": "Use PostgreSQL for primary data",
        },
        timestamp="2026-01-01T00:00:00",
        session_key=None,
        tool="memory_search",
        query="postgres",
        query_hash="abc123",
        day="2026-01-01",
    )

    assert entry["session_key"] == ""
    assert entry["domains"] == ["coding"]
    assert entry["claim_hash"]


def test_event_result_projects_public_fields_only() -> None:
    from src.memory.recall_journal import _event_result

    result = _event_result(
        {
            "target_kind": "kg_rule",
            "target_id": "rule-1",
            "path": "rule-1",
            "score": 0.9,
            "content": "not included",
            "domains": ["coding"],
        }
    )

    assert result == {
        "target_kind": "kg_rule",
        "target_id": "rule-1",
        "path": "rule-1",
        "score": 0.9,
    }


class TestAppendRecallEntries:
    @pytest.mark.asyncio
    async def test_creates_journal_file(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="what did we decide",
            results=[
                {
                    "target_kind": "markdown_section",
                    "target_id": None,
                    "path": "MEMORY:Decisions",
                    "score": 0.8,
                    "domains": [],
                },
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        assert journal.exists()
        entry = json.loads(journal.read_text().strip())
        assert entry["tool"] == "memory_search"
        assert entry["query_hash"]  # SHA1[:12], non-empty
        assert entry["day"]  # YYYY-MM-DD
        assert entry["target_kind"] == "markdown_section"
        assert entry["target_id"] is None

    @pytest.mark.asyncio
    async def test_multiple_results_produce_multiple_lines(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="architecture",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-abc",
                    "path": "",
                    "score": 0.9,
                    "domains": ["coding"],
                },
                {
                    "target_kind": "markdown_section",
                    "target_id": None,
                    "path": "MEMORY:Arch",
                    "score": 0.7,
                    "domains": [],
                },
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        lines = [line for line in journal.read_text().strip().split("\n") if line]
        assert len(lines) == 2
        assert json.loads(lines[0])["target_id"] == "rule-abc"

    @pytest.mark.asyncio
    async def test_domain_rule_get_telemetry(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="domain_rule_get",
            query="rule-xyz",
            results=[
                {
                    "target_kind": "kg_rule",
                    "target_id": "rule-xyz",
                    "path": "",
                    "score": None,
                    "domains": [],
                },
            ],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert entry["target_id"] == "rule-xyz"
        assert entry["score"] is None

    @pytest.mark.asyncio
    async def test_empty_results_no_write(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="nothing",
            results=[],
        )
        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        assert not journal.exists()

    @pytest.mark.asyncio
    async def test_appends_to_existing(self, tmp_path):
        from src.memory.recall_journal import append_recall_entries

        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_text('{"existing": true}\n')

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[
                {
                    "target_kind": "markdown_section",
                    "target_id": None,
                    "path": "",
                    "score": 0.5,
                    "domains": [],
                }
            ],
        )
        lines = [line for line in journal.read_text().strip().split("\n") if line]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_write_errors_are_best_effort(self, monkeypatch, tmp_path):
        import builtins

        from src.memory.recall_journal import append_recall_entries

        def fail_open(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(builtins, "open", fail_open)

        await append_recall_entries(
            workspace=tmp_path,
            session_key="cli:test",
            tool="memory_search",
            query="test",
            results=[
                {
                    "target_kind": "markdown_section",
                    "target_id": None,
                    "path": "",
                    "score": 0.5,
                    "domains": [],
                }
            ],
        )
