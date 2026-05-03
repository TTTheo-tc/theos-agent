"""Tests for cross-session context in consolidation via HISTORY.md."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.consolidation import MemoryConsolidationService
from src.memory.store import MemoryStore
from src.providers.base import LLMResponse, ToolCallRequest
from src.session.manager import Session


def _make_scope(workspace: Path) -> MagicMock:
    scope = MagicMock()
    scope.workspace = workspace
    return scope


def _make_session(key: str, count: int) -> Session:
    session = Session(key=key)
    for i in range(count):
        session.add_message("user", f"msg{i}")
        session.add_message("assistant", f"resp{i}")
    return session


def _make_provider(*, tool_call_args: dict | None = None) -> MagicMock:
    provider = MagicMock()
    args = tool_call_args or {
        "history_entry": "[2026-03-22] Summary of conversation.",
        "memory_update": "## Facts\nSome new fact.",
    }
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content="calling save_memory",
            tool_calls=[
                ToolCallRequest(
                    id="tc1",
                    name="save_memory",
                    arguments=args,
                )
            ],
        )
    )
    return provider


class TestCrossSessionConsolidation:
    """Cross-session context inclusion during consolidation."""

    @pytest.mark.asyncio
    async def test_consolidation_includes_other_session_summaries(self, tmp_path: Path) -> None:
        """When HISTORY.md has entries, the consolidation prompt includes recent activity."""
        store = MemoryStore(tmp_path)
        # Pre-populate HISTORY.md with entries from other sessions
        store.append_history("[2026-03-20 14:00] Discussed project architecture with user.")
        store.append_history("[2026-03-21 10:30] Implemented authentication module.")

        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider()
        session = _make_session("test:cross-session", 30)

        await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        provider.chat.assert_awaited_once()
        call_kwargs = provider.chat.call_args.kwargs
        prompt_text = call_kwargs["messages"][1]["content"]

        # The prompt must include cross-session history
        assert "Recent Project Activity" in prompt_text
        assert "Discussed project architecture" in prompt_text
        assert "Implemented authentication module" in prompt_text

    @pytest.mark.asyncio
    async def test_consolidation_works_without_history(self, tmp_path: Path) -> None:
        """When no HISTORY.md exists, consolidation works normally without errors."""
        store = MemoryStore(tmp_path)
        # Do NOT create HISTORY.md

        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider()
        session = _make_session("test:no-history", 30)

        result = await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        assert result is True
        provider.chat.assert_awaited_once()
        call_kwargs = provider.chat.call_args.kwargs
        prompt_text = call_kwargs["messages"][1]["content"]

        # Should NOT contain the cross-session section
        assert "Recent Project Activity" not in prompt_text

    @pytest.mark.asyncio
    async def test_consolidation_prompt_has_cross_session_rule(self, tmp_path: Path) -> None:
        """The consolidation prompt includes a rule about cross-session awareness."""
        store = MemoryStore(tmp_path)
        store.append_history("[2026-03-20 14:00] Some prior session activity.")

        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider()
        session = _make_session("test:rule-check", 30)

        await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        call_kwargs = provider.chat.call_args.kwargs
        prompt_text = call_kwargs["messages"][1]["content"].lower()

        assert "project-wide awareness" in prompt_text
        assert "recent project activity" in prompt_text


class TestGatherRecentHistory:
    """Unit tests for _gather_recent_history static method."""

    def test_returns_empty_when_no_history_file(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        result = MemoryConsolidationService._gather_recent_history(store)
        assert result == ""

    def test_returns_empty_when_history_file_is_empty(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.history_file.write_text("", encoding="utf-8")
        result = MemoryConsolidationService._gather_recent_history(store)
        assert result == ""

    def test_returns_recent_entries(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.append_history("[2026-03-20] Entry one.")
        store.append_history("[2026-03-21] Entry two.")

        result = MemoryConsolidationService._gather_recent_history(store)
        assert "Recent Project Activity" in result
        assert "Entry one." in result
        assert "Entry two." in result

    def test_respects_max_entries(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        for i in range(15):
            store.append_history(f"[2026-03-{i:02d}] Entry {i}.")

        result = MemoryConsolidationService._gather_recent_history(store, max_entries=3)
        # Should only have the last 3 entries
        assert "Entry 12." in result
        assert "Entry 13." in result
        assert "Entry 14." in result
        assert "Entry 0." not in result
