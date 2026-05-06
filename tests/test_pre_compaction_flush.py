"""Tests for pre-compaction background flush."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_handler(workspace, flush_enabled=True):
    """Create a minimal MemoryHandler for testing pre-flush."""
    from src.agent.loop_memory import MemoryHandler

    config = MagicMock()
    config.memory.flush.enabled = flush_enabled
    config.memory.compaction.enabled = True
    config.memory.compaction.max_consecutive_failures = 3
    config.memory.compaction.threshold_ratio = 0.85
    config.memory.compaction.safety_margin = 0
    config.memory.compaction.restore_max_files = 0
    config.memory.compaction.restore_max_chars_per_file = 0
    scope = MagicMock()
    scope.workspace = workspace

    handler = MemoryHandler.__new__(MemoryHandler)
    handler._memory_config = config.memory
    handler._config = config
    handler._scope = scope
    handler._extract_cursor = {}
    handler._compact_consecutive_failures = {}
    return handler


class TestSchedulePreCompactionFlush:
    @pytest.mark.asyncio
    async def test_skips_when_flush_disabled(self, tmp_path):
        handler = _make_handler(tmp_path, flush_enabled=False)
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(10)],
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_persisted_history_is_none(self, tmp_path):
        handler = _make_handler(tmp_path)
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=None,
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_none_history_skips_before_reading_config(self, tmp_path):
        handler = _make_handler(tmp_path)
        handler._memory_config = None

        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=None,
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_skips_when_gap_too_large(self, tmp_path):
        handler = _make_handler(tmp_path)
        # 60 messages with cursor at 0 → gap > 50, should skip
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(60)],
            compact_prefix_count=55,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_cursor_covers_window(self, tmp_path):
        handler = _make_handler(tmp_path)
        handler._extract_cursor["test"] = 10
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(12)],
            compact_prefix_count=8,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_gap_too_small(self, tmp_path):
        handler = _make_handler(tmp_path)
        await handler._schedule_pre_compaction_flush(
            session_key="test",
            persisted_history=[{"role": "user", "content": "only one"}],
            compact_prefix_count=1,
            provider=AsyncMock(),
            model="test",
            workspace=tmp_path,
        )
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_writes_flush_event_on_success(self, tmp_path):
        handler = _make_handler(tmp_path)
        (tmp_path / "memory" / "MEMORY.md").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "memory" / "MEMORY.md").write_text("# Memory\n")

        mock_facts = [{"section": "Decisions", "content": "We chose X"}]

        with patch(
            "src.agent.loop_memory.extract_durable_facts", new_callable=AsyncMock
        ) as mock_extract:
            mock_extract.return_value = mock_facts
            with patch("src.agent.loop_memory.merge_extracted_facts") as mock_merge:
                mock_merge.return_value = 1
                await handler._schedule_pre_compaction_flush(
                    session_key="test",
                    persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(10)],
                    compact_prefix_count=8,
                    provider=AsyncMock(),
                    model="test",
                    workspace=tmp_path,
                )

        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json"))
        assert len(events) == 1
        data = json.loads(events[0].read_text())
        assert data["type"] == "pre_compaction_flush"
        assert data["facts_merged"] == 1

    @pytest.mark.asyncio
    async def test_extract_failure_is_silent(self, tmp_path):
        handler = _make_handler(tmp_path)

        with patch(
            "src.agent.loop_memory.extract_durable_facts", new_callable=AsyncMock
        ) as mock_extract:
            mock_extract.side_effect = RuntimeError("LLM down")
            # Should not raise
            await handler._schedule_pre_compaction_flush(
                session_key="test",
                persisted_history=[{"role": "user", "content": f"msg {i}"} for i in range(10)],
                compact_prefix_count=8,
                provider=AsyncMock(),
                model="test",
                workspace=tmp_path,
            )
        # No event written on failure
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events = list(events_dir.glob("*-flush.json")) if events_dir.exists() else []
        assert len(events) == 0
