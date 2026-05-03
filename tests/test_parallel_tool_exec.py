"""Tests for guarded parallel tool execution."""

import asyncio
from unittest.mock import AsyncMock, patch

from src.agent.loop_core import run_tool_loop
from src.agent.tools.base import Tool
from src.providers.base import LLMResponse, ToolCallRequest

# ---------------------------------------------------------------------------
# Stub tools
# ---------------------------------------------------------------------------


class ReadTool(Tool):
    @property
    def name(self):
        return "read"

    @property
    def description(self):
        return "read a file"

    @property
    def parameters(self):
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def parallel_safe(self):
        return True

    async def execute(self, **kwargs):
        await asyncio.sleep(0.05)  # simulate I/O
        return f"content of {kwargs.get('path', '?')}"


class WriteTool(Tool):
    @property
    def name(self):
        return "write"

    @property
    def description(self):
        return "write a file"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        }

    # parallel_safe defaults to False

    async def execute(self, **kwargs):
        return "ok"


# ---------------------------------------------------------------------------
# Unit tests: parallel_safe property
# ---------------------------------------------------------------------------


def test_parallel_safe_default_false():
    t = WriteTool()
    assert t.parallel_safe is False


def test_parallel_safe_override_true():
    t = ReadTool()
    assert t.parallel_safe is True


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


class _ParallelRegistry:
    """Minimal ToolRegistry stand-in that tracks execution order."""

    def __init__(self, tool_map: dict[str, Tool]):
        self._tools = tool_map
        self.exec_log: list[str] = []

    def get_definitions(self):
        return [t.to_schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def execute(self, name, params, context=None):
        self.exec_log.append(name)
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: Tool '{name}' not found."
        return await tool.execute(**params)


def _make_provider(*responses):
    """Return an AsyncMock provider whose .chat returns *responses* in order."""
    provider = AsyncMock()
    provider.chat = AsyncMock(side_effect=list(responses))
    return provider


# ---------------------------------------------------------------------------
# Integration tests: parallel vs sequential path
# ---------------------------------------------------------------------------


async def test_parallel_path_taken_when_all_parallel_safe():
    """When every tool call targets a parallel_safe tool, asyncio.gather is used."""
    read_tool = ReadTool()
    registry = _ParallelRegistry({"read": read_tool})

    provider = _make_provider(
        LLMResponse(
            content="reading files",
            tool_calls=[
                ToolCallRequest(id="a", name="read", arguments={"path": "/a"}),
                ToolCallRequest(id="b", name="read", arguments={"path": "/b"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    with patch("src.agent.loop_core.asyncio.gather", wraps=asyncio.gather) as mock_gather:
        content, used, msgs, _ = await run_tool_loop(
            provider=provider,
            tools=registry,
            messages=[{"role": "user", "content": "read two files"}],
            model="test",
            temperature=0,
            max_tokens=1024,
            max_iterations=5,
        )
        mock_gather.assert_called_once()

    assert content == "done"
    assert used == ["read", "read"]


async def test_sequential_path_when_mixed_tools():
    """When any tool is not parallel_safe, fall through to sequential."""
    registry = _ParallelRegistry({"read": ReadTool(), "write": WriteTool()})

    provider = _make_provider(
        LLMResponse(
            content="mixed",
            tool_calls=[
                ToolCallRequest(id="a", name="read", arguments={"path": "/a"}),
                ToolCallRequest(id="b", name="write", arguments={"path": "/x", "content": "y"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    with patch("src.agent.loop_core.asyncio.gather", wraps=asyncio.gather) as mock_gather:
        content, used, msgs, _ = await run_tool_loop(
            provider=provider,
            tools=registry,
            messages=[{"role": "user", "content": "mixed"}],
            model="test",
            temperature=0,
            max_tokens=1024,
            max_iterations=5,
        )
        mock_gather.assert_not_called()

    assert content == "done"
    assert "read" in used
    assert "write" in used


async def test_sequential_path_for_single_tool_call():
    """A single tool call should always go sequential, even if parallel_safe."""
    registry = _ParallelRegistry({"read": ReadTool()})

    provider = _make_provider(
        LLMResponse(
            content="one read",
            tool_calls=[
                ToolCallRequest(id="a", name="read", arguments={"path": "/a"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    with patch("src.agent.loop_core.asyncio.gather", wraps=asyncio.gather) as mock_gather:
        await run_tool_loop(
            provider=provider,
            tools=registry,
            messages=[{"role": "user", "content": "one"}],
            model="test",
            temperature=0,
            max_tokens=1024,
            max_iterations=5,
        )
        mock_gather.assert_not_called()


async def test_parallel_results_in_original_order():
    """Tool results appear in the same order as the original tool_calls."""

    class SlowRead(ReadTool):
        async def execute(self, **kwargs):
            path = kwargs.get("path", "?")
            # Second call sleeps longer to try reordering
            delay = 0.1 if path == "/first" else 0.01
            await asyncio.sleep(delay)
            return f"result:{path}"

    registry = _ParallelRegistry({"read": SlowRead()})

    provider = _make_provider(
        LLMResponse(
            content="reading",
            tool_calls=[
                ToolCallRequest(id="1", name="read", arguments={"path": "/first"}),
                ToolCallRequest(id="2", name="read", arguments={"path": "/second"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    _, _, msgs, _ = await run_tool_loop(
        provider=provider,
        tools=registry,
        messages=[{"role": "user", "content": "read"}],
        model="test",
        temperature=0,
        max_tokens=1024,
        max_iterations=5,
    )

    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "1"
    assert tool_msgs[1]["tool_call_id"] == "2"
    assert "result:/first" in tool_msgs[0]["content"]
    assert "result:/second" in tool_msgs[1]["content"]


# ---------------------------------------------------------------------------
# dedupe_within_turn property tests
# ---------------------------------------------------------------------------


def test_dedupe_within_turn_default_false():
    t = WriteTool()
    assert t.dedupe_within_turn is False


def test_dedupe_within_turn_default_false_for_parallel_safe():
    """parallel_safe does NOT imply dedupe_within_turn."""
    t = ReadTool()
    assert t.parallel_safe is True
    assert t.dedupe_within_turn is False


# ---------------------------------------------------------------------------
# Dedup stub tools
# ---------------------------------------------------------------------------


class _CountingTool(Tool):
    """Tool that counts how many times execute is called."""

    def __init__(self):
        self.call_count = 0

    @property
    def name(self):
        return "counting"

    @property
    def description(self):
        return "counts executions"

    @property
    def parameters(self):
        return {"type": "object", "properties": {"key": {"type": "string"}}}

    @property
    def parallel_safe(self):
        return True

    @property
    def dedupe_within_turn(self):
        return True

    async def execute(self, **kwargs):
        self.call_count += 1
        return f"result:{kwargs.get('key', '?')}:{self.call_count}"


class _CountingNoDedupTool(Tool):
    """Tool that counts executions but does NOT opt in to dedup."""

    def __init__(self):
        self.call_count = 0

    @property
    def name(self):
        return "counting_nodedup"

    @property
    def description(self):
        return "counts executions, no dedup"

    @property
    def parameters(self):
        return {"type": "object", "properties": {"key": {"type": "string"}}}

    @property
    def parallel_safe(self):
        return True

    async def execute(self, **kwargs):
        self.call_count += 1
        return f"result:{kwargs.get('key', '?')}:{self.call_count}"


# ---------------------------------------------------------------------------
# Dedup integration tests
# ---------------------------------------------------------------------------


async def test_dedup_identical_calls_execute_once():
    """Three identical calls to a dedupe_within_turn tool execute once, produce 3 results."""
    counting = _CountingTool()
    registry = _ParallelRegistry({"counting": counting})

    provider = _make_provider(
        LLMResponse(
            content="triple call",
            tool_calls=[
                ToolCallRequest(id="a", name="counting", arguments={"key": "x"}),
                ToolCallRequest(id="b", name="counting", arguments={"key": "x"}),
                ToolCallRequest(id="c", name="counting", arguments={"key": "x"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    content, used, msgs, _ = await run_tool_loop(
        provider=provider,
        tools=registry,
        messages=[{"role": "user", "content": "go"}],
        model="test",
        temperature=0,
        max_tokens=1024,
        max_iterations=5,
    )

    assert content == "done"
    # Tool executed only once
    assert counting.call_count == 1
    # But all 3 tool results present in messages
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 3
    # All have the same result content (from the single execution)
    assert tool_msgs[0]["content"] == tool_msgs[1]["content"] == tool_msgs[2]["content"]
    # Each has its own tool_call_id
    assert tool_msgs[0]["tool_call_id"] == "a"
    assert tool_msgs[1]["tool_call_id"] == "b"
    assert tool_msgs[2]["tool_call_id"] == "c"
    # All 3 appear in tools_used
    assert used == ["counting", "counting", "counting"]


async def test_dedup_different_args_not_collapsed():
    """Calls with different arguments are NOT deduplicated."""
    counting = _CountingTool()
    registry = _ParallelRegistry({"counting": counting})

    provider = _make_provider(
        LLMResponse(
            content="two different",
            tool_calls=[
                ToolCallRequest(id="a", name="counting", arguments={"key": "x"}),
                ToolCallRequest(id="b", name="counting", arguments={"key": "y"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    _, _, msgs, _ = await run_tool_loop(
        provider=provider,
        tools=registry,
        messages=[{"role": "user", "content": "go"}],
        model="test",
        temperature=0,
        max_tokens=1024,
        max_iterations=5,
    )

    assert counting.call_count == 2
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["content"] != tool_msgs[1]["content"]


async def test_no_dedup_for_non_opt_in_tool():
    """Identical calls to a tool without dedupe_within_turn execute every time."""
    counting = _CountingNoDedupTool()
    registry = _ParallelRegistry({"counting_nodedup": counting})

    provider = _make_provider(
        LLMResponse(
            content="triple call",
            tool_calls=[
                ToolCallRequest(id="a", name="counting_nodedup", arguments={"key": "x"}),
                ToolCallRequest(id="b", name="counting_nodedup", arguments={"key": "x"}),
                ToolCallRequest(id="c", name="counting_nodedup", arguments={"key": "x"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    _, _, msgs, _ = await run_tool_loop(
        provider=provider,
        tools=registry,
        messages=[{"role": "user", "content": "go"}],
        model="test",
        temperature=0,
        max_tokens=1024,
        max_iterations=5,
    )

    # All 3 executed
    assert counting.call_count == 3
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 3
    # Each has a different result (incrementing counter)
    assert tool_msgs[0]["content"] != tool_msgs[2]["content"]


async def test_dedup_no_duplicates_passthrough():
    """When there are no duplicates, behavior is unchanged."""
    counting = _CountingTool()
    registry = _ParallelRegistry({"counting": counting})

    provider = _make_provider(
        LLMResponse(
            content="single",
            tool_calls=[
                ToolCallRequest(id="a", name="counting", arguments={"key": "x"}),
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="done", finish_reason="stop"),
    )

    _, used, msgs, _ = await run_tool_loop(
        provider=provider,
        tools=registry,
        messages=[{"role": "user", "content": "go"}],
        model="test",
        temperature=0,
        max_tokens=1024,
        max_iterations=5,
    )

    assert counting.call_count == 1
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert used == ["counting"]
