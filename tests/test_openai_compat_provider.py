"""Tests for OpenAICompatProvider (custom_provider.py)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.providers.base import LLMResponse, StreamDelta, ToolCallRequest
from src.providers.custom_provider import OpenAICompatProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(**overrides: Any) -> OpenAICompatProvider:
    """Create an OpenAICompatProvider with test defaults."""
    defaults: dict[str, Any] = {
        "api_key": "test-key",
        "api_base": "https://test.example.com/v1",
        "default_model": "test-model",
    }
    defaults.update(overrides)
    return OpenAICompatProvider(**defaults)


def _mock_response(
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    """Build a mock OpenAI ChatCompletion response."""
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _mock_tool_call(
    name: str = "read_file",
    arguments: str = '{"path": "/tmp/a.txt"}',
    tc_id: str = "call_abc123",
) -> SimpleNamespace:
    """Build a mock tool call object."""
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


async def _make_async_iter(items: list[Any]):
    """Turn a list into an async iterator."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# TestChat
# ---------------------------------------------------------------------------


class TestChat:
    """Non-streaming chat() tests."""

    @pytest.mark.asyncio
    async def test_basic_chat(self):
        """Normal text response returns LLMResponse with content."""
        provider = _make_provider()
        mock_resp = _mock_response(content="Hello world")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello world"
        assert result.tool_calls == []
        assert result.finish_reason == "stop"
        assert result.error_type is None

    @pytest.mark.asyncio
    async def test_tool_calls_parsed(self):
        """Tool calls in response are parsed into ToolCallRequest objects."""
        provider = _make_provider()
        mock_tc = _mock_tool_call(name="read_file", arguments='{"path": "/tmp/a.txt"}')
        mock_resp = _mock_response(content=None, tool_calls=[mock_tc], finish_reason="tool_calls")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "read a file"}],
            )

        assert result.has_tool_calls
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert isinstance(tc, ToolCallRequest)
        assert tc.name == "read_file"
        assert tc.arguments == {"path": "/tmp/a.txt"}
        assert result.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_usage_tracking(self):
        """Usage stats from the response are propagated."""
        provider = _make_provider()
        mock_resp = _mock_response(
            content="ok",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "count"}],
            )

        assert result.usage["prompt_tokens"] == 100
        assert result.usage["completion_tokens"] == 50
        assert result.usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_reasoning_content(self):
        """reasoning_content from thinking models is captured."""
        provider = _make_provider()
        mock_resp = _mock_response(content="answer", reasoning_content="let me think...")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "reason"}],
            )

        assert result.content == "answer"
        assert result.reasoning_content == "let me think..."

    @pytest.mark.asyncio
    async def test_auth_error_returns_error_type(self):
        """AuthenticationError returns error_type='AuthenticationError'."""
        import openai as openai_mod

        provider = _make_provider()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {}
        auth_err = openai_mod.AuthenticationError(
            message="invalid api key",
            response=mock_response,
            body=None,
        )
        mock_create = AsyncMock(side_effect=auth_err)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.finish_reason == "error"
        assert result.error_type == "AuthenticationError"
        assert "authentication failed" in result.content.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_error_returns_error_type(self):
        """RateLimitError returns error_type='RateLimitError'."""
        import openai as openai_mod

        provider = _make_provider()

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        rate_err = openai_mod.RateLimitError(
            message="rate limited",
            response=mock_response,
            body=None,
        )
        mock_create = AsyncMock(side_effect=rate_err)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.finish_reason == "error"
        assert result.error_type == "RateLimitError"

    @pytest.mark.asyncio
    async def test_generic_error_returns_error_type(self):
        """Generic exception returns error_type with class name."""
        provider = _make_provider()
        mock_create = AsyncMock(side_effect=ConnectionError("connection refused"))

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.finish_reason == "error"
        assert result.error_type == "ConnectionError"
        assert "connection refused" in result.content

    @pytest.mark.asyncio
    async def test_kwargs_forwarded(self):
        """chat() forwards model, max_tokens, temperature, tools."""
        provider = _make_provider()
        mock_resp = _mock_response(content="ok")
        mock_create = AsyncMock(return_value=mock_resp)
        tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]

        with patch.object(provider._client.chat.completions, "create", mock_create):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
                model="custom-model",
                max_tokens=2048,
                temperature=0.5,
            )

        kwargs = mock_create.call_args.kwargs
        assert kwargs["model"] == "custom-model"
        assert kwargs["max_tokens"] == 2048
        assert kwargs["temperature"] == 0.5
        assert kwargs["tools"] == tools
        assert kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_max_tokens_clamped(self):
        """max_tokens=0 is clamped to 1."""
        provider = _make_provider()
        mock_resp = _mock_response(content="ok")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=0,
            )

        kwargs = mock_create.call_args.kwargs
        assert kwargs["max_tokens"] == 1

    @pytest.mark.asyncio
    async def test_text_tool_call_fallback(self):
        """For allowlisted providers, text tool calls are recovered from content."""
        from src.providers.registry import ProviderSpec

        spec = ProviderSpec(
            name="deepseek",
            keywords=("deepseek",),
            env_key="DEEPSEEK_API_KEY",
        )
        provider = _make_provider(spec=spec)

        mock_resp = _mock_response(
            content='<tool_call>{"name": "search", "arguments": {"q": "hello"}}</tool_call>',
            tool_calls=[],
        )
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            result = await provider.chat(
                messages=[{"role": "user", "content": "search"}],
            )

        assert result.has_tool_calls
        assert result.tool_calls[0].name == "search"
        assert result.content is None


# ---------------------------------------------------------------------------
# TestStreaming
# ---------------------------------------------------------------------------


class TestStreaming:
    """chat_stream() tests."""

    def test_supports_streaming(self):
        provider = _make_provider()
        assert provider.supports_streaming is True

    @pytest.mark.asyncio
    async def test_yields_stream_deltas(self):
        """chat_stream yields StreamDelta objects with text content."""
        provider = _make_provider()

        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="Hello", tool_calls=None, reasoning_content=None
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=" world", tool_calls=None, reasoning_content=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
        ]

        mock_create = AsyncMock(return_value=_make_async_iter(chunks))

        with patch.object(provider._client.chat.completions, "create", mock_create):
            deltas: list[StreamDelta] = []
            async for delta in provider.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
            ):
                deltas.append(delta)

        # First two are content deltas, last is the final
        assert len(deltas) == 3
        assert deltas[0].content == "Hello"
        assert deltas[0].is_final is False
        assert deltas[1].content == " world"
        assert deltas[1].is_final is False

        final = deltas[2]
        assert final.is_final is True
        assert final.finish_reason == "stop"
        assert final.usage["prompt_tokens"] == 10
        assert final.usage["completion_tokens"] == 5

    @pytest.mark.asyncio
    async def test_stream_tool_calls(self):
        """Tool call deltas are accumulated and returned in final StreamDelta."""
        provider = _make_provider()

        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_123",
                                    function=SimpleNamespace(name="read_file", arguments='{"pa'),
                                )
                            ],
                            reasoning_content=None,
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(name=None, arguments='th": "/tmp"}'),
                                )
                            ],
                            reasoning_content=None,
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            ),
        ]

        mock_create = AsyncMock(return_value=_make_async_iter(chunks))

        with patch.object(provider._client.chat.completions, "create", mock_create):
            deltas: list[StreamDelta] = []
            async for delta in provider.chat_stream(
                messages=[{"role": "user", "content": "read"}],
            ):
                deltas.append(delta)

        # Only final delta (no text content was emitted)
        assert len(deltas) == 1
        final = deltas[0]
        assert final.is_final is True
        assert len(final.tool_calls) == 1
        tc = final.tool_calls[0]
        assert tc.name == "read_file"
        assert tc.arguments == {"path": "/tmp"}
        assert tc.id == "call_123"
        assert final.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_stream_error_yields_final_delta(self):
        """Errors during streaming yield a final StreamDelta with error_type."""
        provider = _make_provider()
        mock_create = AsyncMock(side_effect=ConnectionError("timeout"))

        with patch.object(provider._client.chat.completions, "create", mock_create):
            deltas: list[StreamDelta] = []
            async for delta in provider.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
            ):
                deltas.append(delta)

        assert len(deltas) == 1
        assert deltas[0].is_final is True
        assert deltas[0].finish_reason == "error"
        assert deltas[0].error_type == "ConnectionError"

    @pytest.mark.asyncio
    async def test_stream_auth_error(self):
        """AuthenticationError during streaming yields error_type='AuthenticationError'."""
        import openai as openai_mod

        provider = _make_provider()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {}
        auth_err = openai_mod.AuthenticationError(
            message="bad key",
            response=mock_response,
            body=None,
        )
        mock_create = AsyncMock(side_effect=auth_err)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            deltas: list[StreamDelta] = []
            async for delta in provider.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
            ):
                deltas.append(delta)

        assert len(deltas) == 1
        assert deltas[0].is_final is True
        assert deltas[0].error_type == "AuthenticationError"

    @pytest.mark.asyncio
    async def test_stream_kwargs_include_stream_options(self):
        """Streaming requests include stream=True and stream_options."""
        provider = _make_provider()
        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="ok", tool_calls=None, reasoning_content=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            ),
        ]
        mock_create = AsyncMock(return_value=_make_async_iter(chunks))

        with patch.object(provider._client.chat.completions, "create", mock_create):
            async for _ in provider.chat_stream(
                messages=[{"role": "user", "content": "hi"}],
            ):
                pass

        kwargs = mock_create.call_args.kwargs
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}


# ---------------------------------------------------------------------------
# TestModelPrefix
# ---------------------------------------------------------------------------


class TestModelPrefix:
    """Model prefix stripping tests."""

    def test_strips_explicit_prefix(self):
        """model_prefix_to_strip removes the prefix."""
        provider = _make_provider(model_prefix_to_strip="custom")
        assert provider._resolve_model("custom/my-model") == "my-model"

    def test_no_strip_when_no_match(self):
        """No stripping when model doesn't start with prefix."""
        provider = _make_provider(model_prefix_to_strip="custom")
        assert provider._resolve_model("other/my-model") == "other/my-model"

    def test_strips_via_spec(self):
        """spec.strip_model_prefix=True strips any prefix."""
        from src.providers.registry import ProviderSpec

        spec = ProviderSpec(
            name="test",
            keywords=(),
            env_key="",
            strip_model_prefix=True,
        )
        provider = _make_provider(spec=spec)
        assert provider._resolve_model("provider/model-name") == "model-name"

    def test_no_strip_by_default(self):
        """Without prefix config, model is unchanged."""
        provider = _make_provider()
        assert provider._resolve_model("my-model") == "my-model"

    def test_explicit_prefix_takes_priority_over_spec(self):
        """model_prefix_to_strip wins over spec.strip_model_prefix."""
        from src.providers.registry import ProviderSpec

        spec = ProviderSpec(
            name="test",
            keywords=(),
            env_key="",
            strip_model_prefix=True,
        )
        provider = _make_provider(model_prefix_to_strip="custom", spec=spec)
        # Explicit prefix matches: strip it
        assert provider._resolve_model("custom/my-model") == "my-model"
        # Explicit prefix doesn't match: fallback to spec (strip_model_prefix)
        assert provider._resolve_model("other/my-model") == "my-model"


# ---------------------------------------------------------------------------
# TestMessageSanitization
# ---------------------------------------------------------------------------


class TestMessageSanitization:
    """Message sanitization tests."""

    def test_strips_non_standard_keys(self):
        """Non-standard keys are removed from messages."""
        messages = [
            {
                "role": "user",
                "content": "hello",
                "metadata": {"source": "test"},
                "custom_field": True,
            }
        ]
        result = OpenAICompatProvider._sanitize_messages(messages)
        assert len(result) == 1
        assert set(result[0].keys()) == {"role", "content"}

    def test_keeps_standard_keys(self):
        """Standard keys (role, content, name, tool_calls, tool_call_id) are preserved."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {
                "role": "tool",
                "content": "result",
                "tool_call_id": "1",
                "name": "f",
            },
        ]
        result = OpenAICompatProvider._sanitize_messages(messages)

        assert "tool_calls" in result[0]
        assert result[1]["tool_call_id"] == "1"
        assert result[1]["name"] == "f"

    def test_adds_content_none_for_assistant_without_content(self):
        """Assistant messages without content get content=None added."""
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}],
            }
        ]
        result = OpenAICompatProvider._sanitize_messages(messages)
        assert result[0]["content"] is None

    def test_preserves_reasoning_content(self):
        """reasoning_content is kept for thinking models."""
        messages = [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "thinking...",
            }
        ]
        result = OpenAICompatProvider._sanitize_messages(messages)
        assert result[0]["reasoning_content"] == "thinking..."


# ---------------------------------------------------------------------------
# TestModelOverrides
# ---------------------------------------------------------------------------


class TestModelOverrides:
    """Model override tests."""

    @pytest.mark.asyncio
    async def test_applies_overrides_from_spec(self):
        """Model overrides from spec are applied to kwargs."""
        from src.providers.registry import ProviderSpec

        spec = ProviderSpec(
            name="test",
            keywords=(),
            env_key="",
            model_overrides=(("kimi-k2.5", {"temperature": 1.0}),),
        )
        provider = _make_provider(spec=spec, default_model="kimi-k2.5")
        mock_resp = _mock_response(content="ok")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.5,
            )

        kwargs = mock_create.call_args.kwargs
        # Override should have replaced the original temperature
        assert kwargs["temperature"] == 1.0

    @pytest.mark.asyncio
    async def test_no_override_when_no_match(self):
        """No override applied when model name doesn't match pattern."""
        from src.providers.registry import ProviderSpec

        spec = ProviderSpec(
            name="test",
            keywords=(),
            env_key="",
            model_overrides=(("kimi-k2.5", {"temperature": 1.0}),),
        )
        provider = _make_provider(spec=spec, default_model="gpt-4o")
        mock_resp = _mock_response(content="ok")
        mock_create = AsyncMock(return_value=mock_resp)

        with patch.object(provider._client.chat.completions, "create", mock_create):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.5,
            )

        kwargs = mock_create.call_args.kwargs
        assert kwargs["temperature"] == 0.5


# ---------------------------------------------------------------------------
# TestDefaultModel
# ---------------------------------------------------------------------------


class TestDefaultModel:
    """get_default_model() tests."""

    def test_returns_default_model(self):
        provider = _make_provider(default_model="my-custom-model")
        assert provider.get_default_model() == "my-custom-model"

    def test_default_default(self):
        """Default value when not specified."""
        provider = OpenAICompatProvider()
        assert provider.get_default_model() == "default"
