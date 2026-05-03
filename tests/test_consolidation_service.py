"""Unit tests for MemoryConsolidationService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.consolidation import _SAVE_MEMORY_TOOL, MemoryConsolidationService
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


def _make_provider(*, tool_call_args: dict | None = None, no_tool_calls: bool = False) -> MagicMock:
    provider = MagicMock()
    if no_tool_calls:
        provider.chat = AsyncMock(return_value=LLMResponse(content="no tool call", tool_calls=[]))
    elif tool_call_args is not None:
        provider.chat = AsyncMock(
            return_value=LLMResponse(
                content="calling save_memory",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="save_memory",
                        arguments=tool_call_args,
                    )
                ],
            )
        )
    else:
        provider.chat = AsyncMock(
            return_value=LLMResponse(
                content="calling save_memory",
                tool_calls=[
                    ToolCallRequest(
                        id="tc1",
                        name="save_memory",
                        arguments={
                            "history_entry": "[2026-03-22] Summary of conversation.",
                            "memory_update": "## Facts\nSome new fact.",
                        },
                    )
                ],
            )
        )
    return provider


class TestConsolidationServiceProviderCall:
    """Test that the service calls provider.chat with the save_memory tool."""

    @pytest.mark.asyncio
    async def test_calls_provider_with_save_memory_tool(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider()
        session = _make_session("test:provider", 30)
        store = MemoryStore(tmp_path)

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
        assert call_kwargs["tools"] == _SAVE_MEMORY_TOOL
        assert call_kwargs["model"] == "test-model"
        # Verify system message mentions consolidation
        messages = call_kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "memory consolidation" in messages[0]["content"].lower()


class TestConsolidationServiceRetry:
    """Test retry on first failure, success on second attempt."""

    @pytest.mark.asyncio
    async def test_retry_on_first_failure_succeeds_on_second(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:retry", 30)
        store = MemoryStore(tmp_path)

        success_response = LLMResponse(
            content="ok",
            tool_calls=[
                ToolCallRequest(
                    id="tc1",
                    name="save_memory",
                    arguments={
                        "history_entry": "[2026-03-22] Retry test.",
                        "memory_update": "## Facts\nRetried successfully.",
                    },
                )
            ],
        )

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=[RuntimeError("transient"), success_response])

        result = await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        assert result is True
        assert provider.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_false(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:exhaust", 30)
        store = MemoryStore(tmp_path)

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("permanent"))

        result = await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        assert result is False
        assert provider.chat.await_count == 2  # max_attempts=2


class TestConsolidationServiceFallback:
    """Test fallback archive when LLM doesn't call save_memory."""

    @pytest.mark.asyncio
    async def test_fallback_when_no_tool_call(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider(no_tool_calls=True)
        session = _make_session("test:fallback", 30)
        store = MemoryStore(tmp_path)

        result = await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        assert result is True
        # Fallback should still write a history entry
        history_text = store.history_file.read_text(encoding="utf-8")
        assert "Archived" in history_text
        assert "messages" in history_text


class TestPersistConsolidationResult:
    """Test that _persist_consolidation_result advances session.last_consolidated."""

    @pytest.mark.asyncio
    async def test_advances_last_consolidated(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:offset", 60)
        store = MemoryStore(tmp_path)

        assert session.last_consolidated == 0

        result = await service._persist_consolidation_result(
            session,
            store=store,
            archive_all=False,
            keep_count=25,
            current_memory="",
            history_entry="[2026-03-22] Test entry.",
            memory_update="## Facts\nNew fact.",
        )

        assert result is True
        # last_consolidated = len(messages) - keep_count = 120 - 25 = 95
        assert session.last_consolidated == len(session.messages) - 25

    @pytest.mark.asyncio
    async def test_archive_all_resets_last_consolidated(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:archive_all", 30)
        session.last_consolidated = 10
        store = MemoryStore(tmp_path)

        result = await service._persist_consolidation_result(
            session,
            store=store,
            archive_all=True,
            keep_count=0,
            current_memory="",
            history_entry="[2026-03-22] Archive all.",
            memory_update=None,
        )

        assert result is True
        assert session.last_consolidated == 0

    @pytest.mark.asyncio
    async def test_writes_history_and_memory(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:writes", 30)
        store = MemoryStore(tmp_path)

        await service._persist_consolidation_result(
            session,
            store=store,
            archive_all=False,
            keep_count=10,
            current_memory="",
            history_entry="[2026-03-22] History entry.",
            memory_update="## Updated\nNew content.",
        )

        history = store.history_file.read_text(encoding="utf-8")
        assert "[2026-03-22] History entry." in history

        memory = store.read_long_term()
        assert "New content." in memory

    @pytest.mark.asyncio
    async def test_marks_sqlite_consolidated(self, tmp_path: Path) -> None:
        """Marking SQLite rows is bookkeeping only.

        Consolidation reads from Session.messages, not from SQLite.
        mark_consolidated() flags rows as processed in the buffer/audit tier
        so they are not re-examined, but the authoritative history is the Session.
        """
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:sqlite", 30)
        store = MemoryStore(tmp_path)

        short_term = AsyncMock()
        short_term.get_unconsolidated = AsyncMock(
            side_effect=[
                [{"id": 1}],  # first call (limit=1)
                [{"id": 1}, {"id": 2}, {"id": 3}],  # second call (all)
            ]
        )
        short_term.mark_consolidated = AsyncMock()

        await service._persist_consolidation_result(
            session,
            store=store,
            archive_all=False,
            keep_count=10,
            current_memory="",
            history_entry="test",
            memory_update=None,
            short_term_store=short_term,
            session_key="test:sqlite",
        )

        short_term.mark_consolidated.assert_awaited_once_with("test:sqlite", 3)

    @pytest.mark.asyncio
    async def test_syncs_memory_index(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        session = _make_session("test:index", 30)
        store = MemoryStore(tmp_path)

        memory_index = AsyncMock()
        memory_index.sync_all = AsyncMock()

        await service._persist_consolidation_result(
            session,
            store=store,
            archive_all=False,
            keep_count=10,
            current_memory="",
            history_entry="test",
            memory_update=None,
            memory_index=memory_index,
        )

        memory_index.sync_all.assert_awaited_once_with(store.memory_dir)


class TestContradictionHandling:
    """Test that the consolidation prompt includes contradiction resolution instructions."""

    @pytest.mark.asyncio
    async def test_prompt_contains_contradiction_handling(self, tmp_path: Path) -> None:
        scope = _make_scope(tmp_path)
        service = MemoryConsolidationService(scope=scope)
        provider = _make_provider()
        session = _make_session("test:contradiction", 30)
        store = MemoryStore(tmp_path)

        await service.consolidate(
            session=session,
            provider=provider,
            model="test-model",
            store=store,
            memory_window=50,
        )

        provider.chat.assert_awaited_once()
        call_kwargs = provider.chat.call_args.kwargs
        prompt_text = call_kwargs["messages"][1]["content"].lower()

        # Prompt must include contradiction resolution guidance
        assert "contradict" in prompt_text, "Prompt should mention contradiction handling"
        assert (
            "newer" in prompt_text or "recent" in prompt_text
        ), "Prompt should instruct to prefer newer/recent information"
