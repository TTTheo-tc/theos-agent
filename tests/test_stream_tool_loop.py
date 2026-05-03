"""Tests for streaming-aware tool loop."""

from __future__ import annotations

import pytest

from src.agent.loop_core import run_tool_loop
from src.providers.base import LLMProvider, LLMResponse, StreamDelta, ToolCallRequest

# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class FakeStreamingProvider(LLMProvider):
    """Provider that supports streaming."""

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)

    @property
    def supports_streaming(self):
        return True

    async def chat(self, messages, **kw):
        return self._responses.pop(0)

    async def chat_stream(self, messages, **kw):
        resp = self._responses.pop(0)
        if isinstance(resp, list):
            # list of StreamDeltas
            for d in resp:
                yield d
        else:
            # LLMResponse -> yield as single final delta
            yield StreamDelta(
                content=resp.content,
                tool_calls=resp.tool_calls,
                is_final=True,
                finish_reason=resp.finish_reason,
                usage=resp.usage,
            )

    def get_default_model(self):
        return "fake/stream-model"


class FakeNonStreamingProvider(LLMProvider):
    """Provider that does NOT support streaming (default)."""

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)

    async def chat(self, messages, **kw):
        return self._responses.pop(0)

    def get_default_model(self):
        return "fake/model"


# ---------------------------------------------------------------------------
# Mock tool registry
# ---------------------------------------------------------------------------


class MockToolRegistry:
    """Minimal ToolRegistry mock that records calls."""

    def __init__(self, results: dict[str, str] | None = None):
        self._results = results or {}
        self.calls: list[tuple[str, dict]] = []

    def get_definitions(self):
        return []

    def get(self, name):
        return None

    async def execute(self, name, params, context=None):
        self.calls.append((name, params))
        return self._results.get(name, f"result of {name}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamingDeltaEmission:
    """Streaming path emits deltas to callback."""

    @pytest.mark.asyncio
    async def test_streaming_path_emits_deltas(self):
        """Provider returns partial deltas then a final one.
        on_content_delta must receive the non-final text chunks,
        and the loop must return the final assembled content.
        """
        deltas = [
            StreamDelta(content="hel"),
            StreamDelta(content="lo"),
            StreamDelta(
                content="hello",
                is_final=True,
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
        provider = FakeStreamingProvider(responses=[deltas])

        received: list[str] = []

        async def on_delta(text: str):
            received.append(text)

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=MockToolRegistry(),
            model="test",
            temperature=0.0,
            max_tokens=128,
            max_iterations=5,
            on_content_delta=on_delta,
        )

        assert received == ["hel", "lo"]
        assert content == "hello"
        assert usage["total_tokens"] == 15


class TestNonStreamingProviderFallback:
    """When the provider does not support streaming, chat() is used even if
    on_content_delta is provided."""

    @pytest.mark.asyncio
    async def test_non_streaming_provider_uses_chat(self):
        provider = FakeNonStreamingProvider(
            responses=[LLMResponse(content="plain answer", finish_reason="stop")]
        )

        delta_called = False

        async def on_delta(text: str):
            nonlocal delta_called
            delta_called = True

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=MockToolRegistry(),
            model="test",
            temperature=0.0,
            max_tokens=128,
            max_iterations=5,
            on_content_delta=on_delta,
        )

        assert content == "plain answer"
        assert delta_called is False


class TestNoDeltaCallbackUsesChat:
    """Even with a streaming provider, if on_content_delta is None,
    the loop must use chat() not chat_stream()."""

    @pytest.mark.asyncio
    async def test_no_delta_callback_uses_chat(self):
        provider = FakeStreamingProvider(
            responses=[LLMResponse(content="via chat", finish_reason="stop")]
        )

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=MockToolRegistry(),
            model="test",
            temperature=0.0,
            max_tokens=128,
            max_iterations=5,
            # on_content_delta intentionally omitted
        )

        assert content == "via chat"
        # Provider should have consumed from _responses via chat(), not chat_stream()
        assert len(provider._responses) == 0


class TestStreamingWithToolCalls:
    """Tool calls work normally when delivered via streaming."""

    @pytest.mark.asyncio
    async def test_tool_calls_with_streaming(self):
        tool_response_deltas = [
            StreamDelta(content="thinking..."),
            StreamDelta(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="call_0", name="my_tool", arguments={"x": 1}),
                ],
                is_final=True,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
        # Second iteration: LLM returns final text (via streaming, single final delta)
        final_deltas = [
            StreamDelta(
                content="done",
                is_final=True,
                finish_reason="stop",
                usage={"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            ),
        ]
        provider = FakeStreamingProvider(responses=[tool_response_deltas, final_deltas])

        received: list[str] = []

        async def on_delta(text: str):
            received.append(text)

        tools = MockToolRegistry(results={"my_tool": "tool output"})

        content, used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "use the tool"}],
            tools=tools,
            model="test",
            temperature=0.0,
            max_tokens=128,
            max_iterations=5,
            on_content_delta=on_delta,
        )

        assert content == "done"
        assert "my_tool" in used
        assert tools.calls == [("my_tool", {"x": 1})]
        # "thinking..." was a non-final delta in the first iteration
        assert "thinking..." in received
