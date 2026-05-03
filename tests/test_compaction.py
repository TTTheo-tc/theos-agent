"""Tests for MemoryHandler.maybe_compact() compaction hardening."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch target for MemoryStore.compact_messages (imported inside maybe_compact)
_COMPACT_PATCH = "src.memory.store.MemoryStore.compact_messages"


def _make_handler(
    *,
    enabled: bool = True,
    threshold_ratio: float = 0.85,
    safety_margin: float = 1.0,
    max_consecutive_failures: int = 3,
) -> Any:
    """Create a MemoryHandler with minimal config for compaction tests."""
    from src.agent.loop_memory import MemoryHandler

    memory_config = MagicMock()
    memory_config.compaction.enabled = enabled
    memory_config.compaction.threshold_ratio = threshold_ratio
    memory_config.compaction.safety_margin = safety_margin
    memory_config.compaction.max_consecutive_failures = max_consecutive_failures
    memory_config.compaction.restore_max_files = 5
    memory_config.compaction.restore_max_chars_per_file = 20_000

    orchestrator_config = MagicMock()
    orchestrator_config.memory_tiers.enabled = False

    workspace = Path("/tmp/test_compaction_workspace")
    workspace.mkdir(parents=True, exist_ok=True)

    handler = MemoryHandler(
        workspace=workspace,
        memory_config=memory_config,
        orchestrator_config=orchestrator_config,
        group_memory_enabled=False,
        groups_base_dir=workspace / "groups",
    )
    return handler


def _make_messages(n: int, *, chars_per_msg: int = 5000) -> list[dict]:
    """Build a message list: system + n user/assistant pairs + final user."""
    msgs: list[dict] = [{"role": "system", "content": "You are helpful."}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"Question {i}: " + "x" * chars_per_msg})
        msgs.append({"role": "assistant", "content": f"Answer {i}: " + "y" * chars_per_msg})
    msgs.append({"role": "user", "content": "Final question"})
    return msgs


def _mock_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.chat.return_value = MagicMock(content="Summary of prior conversation.")
    return provider


class TestCircuitBreaker:
    """Circuit breaker stops compaction after N consecutive failures."""

    @pytest.mark.asyncio
    async def test_consecutive_failures_stop_compaction(self):
        """After 3 failures, the 4th call skips without calling compact_messages."""
        handler = _make_handler(max_consecutive_failures=3)
        provider = _mock_provider()
        msgs = _make_messages(10)

        mock_compact = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=200_000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, mock_compact),
        ):
            # 3 calls should each attempt compact_messages and fail
            for _ in range(3):
                result = await handler.maybe_compact(
                    msgs,
                    provider=provider,
                    model="test-model",
                    memory_window=200_000,
                    session_key="s1",
                )
                assert result is msgs  # original messages returned on failure

            # 4th call should skip entirely — circuit breaker open
            result = await handler.maybe_compact(
                msgs,
                provider=provider,
                model="test-model",
                memory_window=200_000,
                session_key="s1",
            )
            assert result is msgs

        # compact_messages was called exactly 3 times (not 4)
        assert mock_compact.call_count == 3

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """After 2 failures then 1 success, failure counter resets to 0."""
        handler = _make_handler(max_consecutive_failures=3)
        provider = _mock_provider()
        msgs = _make_messages(10)

        mock_compact_fail = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        mock_compact_ok = AsyncMock(return_value="Summary of prior conversation.")

        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=200_000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
        ):
            # 2 failures
            with patch(_COMPACT_PATCH, mock_compact_fail):
                for _ in range(2):
                    await handler.maybe_compact(
                        msgs,
                        provider=provider,
                        model="test-model",
                        memory_window=200_000,
                        session_key="s1",
                    )
            assert handler._compact_consecutive_failures.get("s1", 0) == 2

            # 1 success
            with patch(_COMPACT_PATCH, mock_compact_ok):
                await handler.maybe_compact(
                    msgs,
                    provider=provider,
                    model="test-model",
                    memory_window=200_000,
                    session_key="s1",
                )
            assert handler._compact_consecutive_failures.get("s1", 0) == 0

    @pytest.mark.asyncio
    async def test_below_threshold_skips_without_touching_breaker(self):
        """Below-threshold calls don't increment the breaker counter."""
        handler = _make_handler(max_consecutive_failures=3)
        provider = _mock_provider()
        msgs = _make_messages(2)

        mock_compact = AsyncMock()

        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=1000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, mock_compact),
        ):
            result = await handler.maybe_compact(
                msgs, provider=provider, model="test-model", memory_window=200_000
            )
            assert result is msgs
            assert handler._compact_consecutive_failures.get("", 0) == 0
            assert mock_compact.call_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_is_per_session(self):
        """Failures in session A must not trip the breaker for session B."""
        handler = _make_handler(max_consecutive_failures=3)
        provider = _mock_provider()
        msgs = _make_messages(10)

        mock_compact_fail = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        mock_compact_ok = AsyncMock(return_value="Summary")

        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=200_000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, mock_compact_fail),
        ):
            # Trip breaker for session A
            for _ in range(3):
                await handler.maybe_compact(
                    msgs,
                    provider=provider,
                    model="test-model",
                    memory_window=200_000,
                    session_key="session_a",
                )

        # Session A is tripped
        assert handler._compact_consecutive_failures.get("session_a", 0) == 3

        # Session B should still work
        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=200_000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, mock_compact_ok),
        ):
            result = await handler.maybe_compact(
                msgs,
                provider=provider,
                model="test-model",
                memory_window=200_000,
                session_key="session_b",
            )
            assert len(result) < len(msgs)  # compaction happened for session B


class TestMicrocompaction:
    """Old tool results should shrink before full LLM compaction is needed."""

    @pytest.mark.asyncio
    async def test_microcompaction_can_avoid_llm_compaction(self):
        handler = _make_handler()
        provider = _mock_provider()
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "turn1"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "old",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "old", "name": "grep", "content": "A" * 12000},
            {"role": "user", "content": "turn2"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "recent",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "recent", "name": "read_file", "content": "B" * 2500},
            {"role": "user", "content": "final"},
        ]

        mock_compact = AsyncMock(return_value="Summary")
        with (
            patch(
                "src.memory.token_budget.estimate_messages_tokens", side_effect=[180_000, 120_000]
            ),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, mock_compact),
        ):
            result = await handler.maybe_compact(
                msgs, provider=provider, model="test-model", memory_window=200_000
            )

        assert mock_compact.call_count == 0
        old_tool = next(m for m in result if m.get("tool_call_id") == "old")
        recent_tool = next(m for m in result if m.get("tool_call_id") == "recent")
        assert "microcompacted" in old_tool["content"]
        assert recent_tool["content"] == "B" * 2500

    @pytest.mark.asyncio
    async def test_microcompaction_skips_recent_turn_tool_results(self):
        handler = _make_handler()
        provider = _mock_provider()
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "turn1"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "turn2"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "recent",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "recent", "name": "grep", "content": "C" * 12000},
            {"role": "user", "content": "final"},
        ]

        with (
            patch("src.memory.token_budget.estimate_messages_tokens", return_value=180_000),
            patch("src.memory.token_budget.resolve_context_limit", return_value=200_000),
            patch(_COMPACT_PATCH, AsyncMock(return_value="Summary")),
        ):
            result = await handler.maybe_compact(
                msgs, provider=provider, model="test-model", memory_window=200_000
            )

        recent_tool = next(m for m in result if m.get("tool_call_id") == "recent")
        assert "microcompacted" not in recent_tool["content"]


# -- Invariant guard tests (9c2) ----------------------------------------------


def _make_tool_call_messages() -> list[dict]:
    """Build messages with tool_use/tool_result pairs that must not be split."""
    return [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Q1 " + "x" * 5000},
        {"role": "assistant", "content": "A1 " + "y" * 5000},
        {"role": "user", "content": "Q2 " + "x" * 5000},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "foo.py"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file contents " + "z" * 5000},
        {"role": "user", "content": "Q3 " + "x" * 5000},
        {"role": "assistant", "content": "A3 " + "y" * 5000},
        {"role": "user", "content": "Q4 " + "x" * 5000},
        {"role": "assistant", "content": "A4 " + "y" * 5000},
        {"role": "user", "content": "Final question"},
    ]


class TestInvariantGuard:
    """Compaction must never orphan tool_result from its tool_use."""

    @pytest.mark.asyncio
    async def test_cut_does_not_split_tool_pair(self):
        """If naive ~50% lands inside a tool_use/tool_result pair, cut adjusts."""
        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_tool_call_messages()

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )
        # Compaction happened
        assert len(result) < len(msgs)

        # Verify no orphaned tool messages in kept portion
        kept = result[3:]  # skip system + summary pair
        tool_call_ids: set[str] = set()
        tool_result_ids: set[str] = set()
        for m in kept:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tool_call_ids.add(tc["id"])
            if m.get("role") == "tool" and m.get("tool_call_id"):
                tool_result_ids.add(m["tool_call_id"])

        # Every tool_result must have a matching tool_call in the kept range
        orphaned = tool_result_ids - tool_call_ids
        assert not orphaned, f"Orphaned tool_result IDs: {orphaned}"

    @pytest.mark.asyncio
    async def test_cut_before_tool_pair_keeps_pair_intact(self):
        """Cut should move before the tool_use msg, not between tool_use and tool_result."""
        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_tool_call_messages()

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        # The tool pair (tool_calls msg + tool result) must both be in the
        # kept or both in the summarized portion — never split.
        kept = result[3:]
        has_tool_call = any(m.get("tool_calls") for m in kept)
        has_tool_result = any(m.get("role") == "tool" for m in kept)
        # Both present or both absent
        assert has_tool_call == has_tool_result

    @pytest.mark.asyncio
    async def test_multiple_tool_pairs_preserved(self):
        """Multiple tool pairs in history should all be kept intact."""
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "u1 " + "x" * 3000},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "r1 " + "z" * 3000},
            {"role": "user", "content": "u2 " + "x" * 3000},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c2", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c2", "content": "r2 " + "z" * 3000},
            {"role": "user", "content": "u3 " + "x" * 3000},
            {"role": "assistant", "content": "a3 " + "y" * 3000},
            {"role": "user", "content": "Final"},
        ]
        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        kept = result[3:]
        tool_call_ids = set()
        tool_result_ids = set()
        for m in kept:
            for tc in m.get("tool_calls") or []:
                tool_call_ids.add(tc["id"])
            if m.get("role") == "tool":
                tool_result_ids.add(m["tool_call_id"])
        orphaned = tool_result_ids - tool_call_ids
        assert not orphaned, f"Orphaned tool_result IDs: {orphaned}"


# -- Post-compact context restoration tests (9a) ------------------------------


class TestPostCompactRestoration:
    """After compaction, recently-read files should be re-injected."""

    @pytest.mark.asyncio
    async def test_recent_reads_restored_after_compaction(self):
        from src.agent.context import ContextBuilder
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        # Simulate that 2 files were read during this session.
        # _read_state is keyed by session_key; tests use the default empty key.
        ReadFileTool.clear_read_state()
        tmp = Path("/tmp/test_compaction_workspace")
        f1 = tmp / "src" / "main.py"
        f2 = tmp / "src" / "utils.py"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("def main():\n    pass\n")
        f2.write_text("def helper():\n    return 42\n")
        ReadFileTool._read_state.setdefault(None, {})[str(f1)] = (f1.stat().st_mtime, None, None)
        ReadFileTool._read_state[None][str(f2)] = (f2.stat().st_mtime, None, None)

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        # Compaction happened
        assert len(result) < len(msgs)

        # The post-compact messages should include file restoration context
        restoration_msgs = [
            m
            for m in result
            if m.get("role") == "user" and "[Recently read files" in str(m.get("content", ""))
        ]
        assert len(restoration_msgs) == 1
        content = restoration_msgs[0]["content"]
        assert content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
        assert "main.py" in content
        assert "utils.py" in content

        # Cleanup
        ReadFileTool.clear_read_state()

    @pytest.mark.asyncio
    async def test_no_restoration_when_no_reads(self):
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        ReadFileTool.clear_read_state()

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        assert len(result) < len(msgs)
        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        assert len(restoration) == 0

    @pytest.mark.asyncio
    async def test_restoration_respects_token_budget(self):
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        # Create a file that exceeds per-file budget
        ReadFileTool.clear_read_state()
        tmp = Path("/tmp/test_compaction_workspace")
        big_file = tmp / "big.py"
        big_file.parent.mkdir(parents=True, exist_ok=True)
        big_file.write_text("x" * 100_000)  # 100KB
        ReadFileTool._read_state.setdefault(None, {})[str(big_file)] = (
            big_file.stat().st_mtime,
            None,
            None,
        )

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        # Restoration should exist but be truncated
        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        assert len(restoration) == 1
        content = restoration[0]["content"]
        assert "truncated" in content.lower() or len(content) < 100_000

        ReadFileTool.clear_read_state()

    @pytest.mark.asyncio
    async def test_restoration_skips_missing_files(self):
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        # Reference a file under workspace that no longer exists
        ReadFileTool.clear_read_state()
        ReadFileTool._read_state.setdefault(None, {})[
            "/tmp/test_compaction_workspace/nonexistent_file.py"
        ] = (0.0, None, None)

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        # Should compact fine, skip missing file
        assert len(result) < len(msgs)
        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        # No restoration since the only file is missing
        assert len(restoration) == 0

        ReadFileTool.clear_read_state()

    @pytest.mark.asyncio
    async def test_restoration_only_includes_workspace_files(self):
        """Files outside workspace must not be restored (session isolation)."""
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        ReadFileTool.clear_read_state()
        # File inside workspace — should be included
        tmp = Path("/tmp/test_compaction_workspace")
        inside = tmp / "inside.py"
        inside.write_text("inside content")
        session_state = ReadFileTool._read_state.setdefault(None, {})
        session_state[str(inside)] = (inside.stat().st_mtime, None, None)
        # File outside workspace — should be excluded
        outside = Path("/tmp/outside_workspace_file.py")
        outside.write_text("outside content")
        session_state[str(outside)] = (outside.stat().st_mtime, None, None)

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        assert len(restoration) == 1
        content = restoration[0]["content"]
        assert "inside.py" in content
        assert "outside content" not in content

        ReadFileTool.clear_read_state()
        inside.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_restoration_skips_modified_files(self):
        """Files modified after read should not be restored (stale content)."""
        import time

        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        ReadFileTool.clear_read_state()
        tmp = Path("/tmp/test_compaction_workspace")
        f = tmp / "changed.py"
        f.write_text("original")
        old_mtime = f.stat().st_mtime
        ReadFileTool._read_state.setdefault(None, {})[str(f)] = (old_mtime, None, None)

        # Modify the file so mtime changes
        time.sleep(0.05)
        f.write_text("modified content")

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        # File was modified — should be skipped
        assert len(restoration) == 0

        ReadFileTool.clear_read_state()
        f.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_restoration_honors_offset_limit(self):
        """Restoration should replay only the lines the model originally saw."""
        from src.agent.tools.fs_read import ReadFileTool

        handler = _make_handler(threshold_ratio=0.01)
        provider = _mock_provider()
        msgs = _make_messages(10)

        ReadFileTool.clear_read_state()
        tmp = Path("/tmp/test_compaction_workspace")
        f = tmp / "partial.py"
        # 10 lines, but model only read lines 3-5 (offset=3, limit=3)
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        ReadFileTool._read_state.setdefault(None, {})[str(f)] = (f.stat().st_mtime, 3, 3)

        result = await handler.maybe_compact(
            msgs, provider=provider, model="claude-sonnet", memory_window=50
        )

        restoration = [m for m in result if "[Recently read files" in str(m.get("content", ""))]
        assert len(restoration) == 1
        content = restoration[0]["content"]
        # Should contain lines 3-5, not lines 1-2 or 6-10
        assert "line3" in content
        assert "line4" in content
        assert "line5" in content
        assert "line1" not in content
        assert "line6" not in content

        ReadFileTool.clear_read_state()
        f.unlink(missing_ok=True)
