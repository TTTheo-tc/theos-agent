"""Tests for AnthropicProvider — native Anthropic SDK integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# Helpers: lightweight stand-ins for Anthropic SDK types
# ---------------------------------------------------------------------------


@dataclass
class FakeTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class FakeToolUseBlock:
    type: str = "tool_use"
    id: str = "toolu_abc123"
    name: str = "read_file"
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeThinkingBlock:
    type: str = "thinking"
    thinking: str = ""
    signature: str = ""


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeMessage:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)
    id: str = "msg_123"
    model: str = "claude-sonnet-4-20250514"
    role: str = "assistant"
    type: str = "message"


# Streaming event fakes


@dataclass
class FakeContentBlockStartEvent:
    type: str = "content_block_start"
    content_block: Any = None
    index: int = 0


@dataclass
class FakeTextDelta:
    type: str = "text_delta"
    text: str = ""


@dataclass
class FakeThinkingDelta:
    type: str = "thinking_delta"
    thinking: str = ""


@dataclass
class FakeInputJSONDelta:
    type: str = "input_json_delta"
    partial_json: str = ""


@dataclass
class FakeContentBlockDeltaEvent:
    type: str = "content_block_delta"
    delta: Any = None
    index: int = 0


@dataclass
class FakeContentBlockStopEvent:
    type: str = "content_block_stop"
    index: int = 0


@dataclass
class FakeMessageDeltaStop:
    stop_reason: str = "end_turn"


@dataclass
class FakeMessageDeltaUsage:
    output_tokens: int = 50
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeMessageDeltaEvent:
    type: str = "message_delta"
    delta: Any = None
    usage: Any = None


@dataclass
class FakeMessageStartEvent:
    type: str = "message_start"
    message: Any = None


@dataclass
class FakeToolUseStartBlock:
    type: str = "tool_use"
    id: str = "toolu_stream1"
    name: str = "write_file"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """Create an AnthropicProvider with a mocked client."""
    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock()
        mock_client.messages.stream = MagicMock()

        p = AnthropicProvider(api_key="sk-ant-test123", default_model="claude-sonnet-4-20250514")
        p._client = mock_client
        yield p


@pytest.fixture
def provider_with_caching():
    """Create an AnthropicProvider with prompt caching enabled."""
    spec = MagicMock()
    spec.supports_prompt_caching = True

    with patch("anthropic.AsyncAnthropic"):
        p = AnthropicProvider(
            api_key="sk-ant-test123",
            default_model="claude-sonnet-4-20250514",
            spec=spec,
        )
        p._client = MagicMock()
        p._client.messages = MagicMock()
        p._client.messages.create = AsyncMock()
        yield p


# ===========================================================================
# TestMessageConversion
# ===========================================================================


class TestMessageConversion:
    """Test _convert_messages: system extraction, tool_calls, tool results."""

    def test_system_extraction(self, provider):
        """System messages are extracted to a separate parameter."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, msgs = provider._convert_messages(messages)
        assert system == "You are helpful."
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_multiple_system_messages(self, provider):
        """Multiple system messages are accumulated."""
        messages = [
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "Hi"},
        ]
        system, msgs = provider._convert_messages(messages)
        assert isinstance(system, list)
        assert len(system) == 2
        assert system[0]["text"] == "Rule 1"
        assert system[1]["text"] == "Rule 2"

    def test_tool_calls_to_tool_use(self, provider):
        """Assistant tool_calls → Anthropic tool_use blocks."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test.py"}',
                        },
                    }
                ],
            }
        ]
        _, msgs = provider._convert_messages(messages)
        assert len(msgs) == 1
        blocks = msgs[0]["content"]
        # Should have text block (empty placeholder) + tool_use block
        tool_use_blocks = [b for b in blocks if b["type"] == "tool_use"]
        assert len(tool_use_blocks) == 1
        assert tool_use_blocks[0]["name"] == "read_file"
        assert tool_use_blocks[0]["id"] == "call_1"
        assert tool_use_blocks[0]["input"] == {"path": "/tmp/test.py"}

    def test_tool_result_to_user_message(self, provider):
        """Tool results → user message with tool_result block."""
        messages = [
            {"role": "user", "content": "Read the file"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "file contents here",
            },
        ]
        _, msgs = provider._convert_messages(messages)
        # Last message should be user with tool_result
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert len(user_msgs) >= 1
        last_user = user_msgs[-1]
        assert isinstance(last_user["content"], list)
        tool_results = [b for b in last_user["content"] if b.get("type") == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_use_id"] == "call_1"
        assert tool_results[0]["content"] == "file contents here"

    def test_image_url_base64_conversion(self, provider):
        """image_url blocks with base64 data are converted to Anthropic image blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBOR..."},
                    },
                ],
            }
        ]
        _, msgs = provider._convert_messages(messages)
        content = msgs[0]["content"]
        image_blocks = [b for b in content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"
        assert image_blocks[0]["source"]["media_type"] == "image/png"

    def test_image_url_web_conversion(self, provider):
        """image_url blocks with HTTP URLs are converted to Anthropic URL source."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"},
                    },
                ],
            }
        ]
        _, msgs = provider._convert_messages(messages)
        content = msgs[0]["content"]
        image_blocks = [b for b in content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "url"
        assert image_blocks[0]["source"]["url"] == "https://example.com/img.png"

    def test_consecutive_user_messages_merged(self, provider):
        """Consecutive same-role messages are merged (Anthropic requirement)."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Second"},
        ]
        _, msgs = provider._convert_messages(messages)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_empty_content_handled(self, provider):
        """Empty/None content is handled gracefully."""
        messages = [
            {"role": "user", "content": None},
        ]
        _, msgs = provider._convert_messages(messages)
        assert msgs[0]["content"] == "(empty)"


# ===========================================================================
# TestToolConversion
# ===========================================================================


class TestToolConversion:
    """Test _convert_tools: OpenAI → Anthropic format."""

    def test_basic_conversion(self, provider):
        """Standard OpenAI tool definition → Anthropic format."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from disk",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            }
        ]
        result = provider._convert_tools(tools)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "read_file"
        assert result[0]["description"] == "Read a file from disk"
        assert result[0]["input_schema"]["type"] == "object"
        assert "path" in result[0]["input_schema"]["properties"]

    def test_none_tools(self, provider):
        """None tools returns None."""
        assert provider._convert_tools(None) is None

    def test_empty_tools(self, provider):
        """Empty list returns None."""
        assert provider._convert_tools([]) is None

    def test_no_description(self, provider):
        """Tool without description omits the key."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        result = provider._convert_tools(tools)
        assert "description" not in result[0]

    def test_cache_control_passthrough(self, provider):
        """cache_control on the tool is preserved."""
        tools = [
            {
                "type": "function",
                "function": {"name": "t1", "parameters": {}},
                "cache_control": {"type": "ephemeral"},
            }
        ]
        result = provider._convert_tools(tools)
        assert result[0]["cache_control"] == {"type": "ephemeral"}


# ===========================================================================
# TestUsageNormalization
# ===========================================================================


class TestUsageNormalization:
    """Test _normalize_usage: Anthropic → standard field names."""

    def test_basic_normalization(self, provider):
        usage = FakeUsage(input_tokens=100, output_tokens=50)
        result = provider._normalize_usage(usage)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150

    def test_cache_fields_included(self, provider):
        usage = FakeUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=80,
        )
        result = provider._normalize_usage(usage)
        assert result["cache_creation_input_tokens"] == 200
        assert result["cache_read_input_tokens"] == 80

    def test_cache_fields_omitted_when_zero(self, provider):
        usage = FakeUsage(input_tokens=10, output_tokens=5)
        result = provider._normalize_usage(usage)
        assert "cache_creation_input_tokens" not in result
        assert "cache_read_input_tokens" not in result

    def test_none_usage(self, provider):
        assert provider._normalize_usage(None) == {}


# ===========================================================================
# TestChat
# ===========================================================================


class TestChat:
    """Test chat() with mocked client.messages.create."""

    async def test_basic_chat(self, provider):
        """Simple text response is parsed correctly."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[FakeTextBlock(text="Hello!")],
            stop_reason="end_turn",
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello!"
        assert result.finish_reason == "stop"
        assert result.usage["prompt_tokens"] == 10
        assert result.usage["completion_tokens"] == 5

    async def test_tool_use_response(self, provider):
        """Tool use blocks are parsed into ToolCallRequest."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[
                FakeToolUseBlock(
                    id="toolu_abc",
                    name="read_file",
                    input={"path": "/tmp/test.py"},
                )
            ],
            stop_reason="tool_use",
        )

        result = await provider.chat(
            messages=[{"role": "user", "content": "Read test.py"}],
        )

        assert result.has_tool_calls
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "read_file"
        assert result.tool_calls[0].arguments == {"path": "/tmp/test.py"}
        assert result.finish_reason == "tool_calls"

    async def test_thinking_response(self, provider):
        """Thinking blocks are extracted as reasoning_content."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[
                FakeThinkingBlock(thinking="Let me think..."),
                FakeTextBlock(text="The answer is 42."),
            ],
            stop_reason="end_turn",
        )

        result = await provider.chat(
            messages=[{"role": "user", "content": "What is the meaning of life?"}],
        )

        assert result.content == "The answer is 42."
        assert result.reasoning_content == "Let me think..."

    async def test_model_prefix_stripped(self, provider):
        """anthropic/ prefix is stripped before sending to the API."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[FakeTextBlock(text="ok")]
        )

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            model="anthropic/claude-sonnet-4-20250514",
        )

        call_kwargs = provider._client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-20250514"

    async def test_tools_passed_to_api(self, provider):
        """Tools are converted and passed to the API."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[FakeTextBlock(text="ok")]
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = provider._client.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "read_file"
        assert call_kwargs["tool_choice"] == {"type": "auto"}

    async def test_max_tokens_clamped(self, provider):
        """max_tokens is clamped to at least 1."""
        provider._client.messages.create.return_value = FakeMessage(
            content=[FakeTextBlock(text="ok")]
        )

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=0,
        )

        call_kwargs = provider._client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1


# ===========================================================================
# TestErrorHandling
# ===========================================================================


class TestErrorHandling:
    """Test error handling in chat()."""

    def test_constructor_rejects_oauth_token(self):
        """Anthropic OAuth tokens fail closed at provider construction."""
        with patch("anthropic.AsyncAnthropic"):
            with pytest.raises(ValueError, match="Anthropic OAuth tokens are disabled"):
                AnthropicProvider(api_key="sk-ant-oat01-test")

    async def test_auth_error(self, provider):
        """Authentication error returns error_type='AuthenticationError'."""
        from anthropic import AuthenticationError

        provider._client.messages.create.side_effect = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body={"error": {"message": "Invalid API key"}},
        )

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.error_type == "AuthenticationError"
        assert result.finish_reason == "error"
        assert "authentication failed" in result.content.lower()

    async def test_auth_error_retries_once_then_fails(self, provider):
        """After 401, retry once with the same API key before returning an error."""
        from anthropic import AuthenticationError

        err_body = {
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid"},
        }
        provider._client.messages.create = AsyncMock(
            side_effect=AuthenticationError(
                response=MagicMock(status_code=401), body=err_body, message="auth"
            )
        )

        result = await provider.chat([{"role": "user", "content": "hi"}])
        assert result.error_type == "AuthenticationError"
        assert provider._client.messages.create.call_count == 2

    async def test_generic_error(self, provider):
        """Generic exceptions are caught and returned as error responses."""
        provider._client.messages.create.side_effect = RuntimeError("Connection failed")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.error_type == "RuntimeError"
        assert result.finish_reason == "error"
        assert "Connection failed" in result.content


# ===========================================================================
# TestPromptCaching
# ===========================================================================


class TestPromptCaching:
    """Test _apply_cache_control markers."""

    def test_system_string_cached(self, provider):
        """String system content gets cache_control marker."""
        system, msgs, tools = provider._apply_cache_control(
            "You are helpful.",
            [{"role": "user", "content": "Hi"}],
            None,
        )
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == "You are helpful."

    def test_system_list_cached(self, provider):
        """List system content gets cache_control on last block."""
        system_blocks = [
            {"type": "text", "text": "Rule 1"},
            {"type": "text", "text": "Rule 2"},
        ]
        system, _, _ = provider._apply_cache_control(
            system_blocks,
            [{"role": "user", "content": "Hi"}],
            None,
        )
        assert isinstance(system, list)
        # Only last block gets marker
        assert "cache_control" not in system[0]
        assert system[1]["cache_control"] == {"type": "ephemeral"}

    def test_last_user_message_cached(self, provider):
        """Last user message gets cache_control marker."""
        _, msgs, _ = provider._apply_cache_control(
            "",
            [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Second question"},
            ],
            None,
        )
        # Last user message should be cached
        last_user = msgs[2]
        assert isinstance(last_user["content"], list)
        assert last_user["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_tools_cached(self, provider):
        """Last tool definition gets cache_control marker."""
        tools = [
            {"name": "tool1", "input_schema": {}},
            {"name": "tool2", "input_schema": {}},
        ]
        _, _, new_tools = provider._apply_cache_control("", [], tools)
        assert "cache_control" not in new_tools[0]
        assert new_tools[1]["cache_control"] == {"type": "ephemeral"}

    async def test_caching_applied_in_chat(self, provider_with_caching):
        """When spec.supports_prompt_caching=True, cache_control is applied in chat()."""
        p = provider_with_caching
        p._client.messages.create = AsyncMock(
            return_value=FakeMessage(content=[FakeTextBlock(text="ok")])
        )

        await p.chat(
            messages=[
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "Hello"},
            ],
        )

        call_kwargs = p._client.messages.create.call_args.kwargs
        # System should be a list with cache_control
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0].get("cache_control") == {"type": "ephemeral"}


# ===========================================================================
# TestChatStream
# ===========================================================================


class TestChatStream:
    """Test chat_stream() yielding StreamDelta chunks."""

    async def test_text_streaming(self, provider):
        """Text deltas yield StreamDelta(content=...) chunks."""
        events = [
            FakeContentBlockStartEvent(
                content_block=FakeTextBlock(type="text", text=""),
                index=0,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeTextDelta(text="Hello "),
                index=0,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeTextDelta(text="world!"),
                index=0,
            ),
            FakeContentBlockStopEvent(index=0),
            FakeMessageDeltaEvent(
                delta=FakeMessageDeltaStop(stop_reason="end_turn"),
                usage=FakeMessageDeltaUsage(output_tokens=10),
            ),
        ]

        # Mock the stream context manager
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = lambda self: self
        mock_stream._events = iter(events)

        async def _anext(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

        mock_stream.__anext__ = _anext
        provider._client.messages.stream.return_value = mock_stream

        deltas = []
        async for delta in provider.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
        ):
            deltas.append(delta)

        # Should get text deltas + final
        text_deltas = [d for d in deltas if d.content and not d.is_final]
        assert len(text_deltas) == 2
        assert text_deltas[0].content == "Hello "
        assert text_deltas[1].content == "world!"

        final = deltas[-1]
        assert final.is_final
        assert final.finish_reason == "stop"

    async def test_tool_streaming(self, provider):
        """Tool use blocks are accumulated and yielded in final delta."""
        events = [
            FakeContentBlockStartEvent(
                content_block=FakeToolUseStartBlock(id="toolu_stream1", name="write_file"),
                index=0,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeInputJSONDelta(partial_json='{"path":'),
                index=0,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeInputJSONDelta(partial_json=' "/tmp/f.txt"}'),
                index=0,
            ),
            FakeContentBlockStopEvent(index=0),
            FakeMessageDeltaEvent(
                delta=FakeMessageDeltaStop(stop_reason="tool_use"),
                usage=FakeMessageDeltaUsage(output_tokens=20),
            ),
        ]

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = lambda self: self
        mock_stream._events = iter(events)

        async def _anext(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

        mock_stream.__anext__ = _anext
        provider._client.messages.stream.return_value = mock_stream

        deltas = []
        async for delta in provider.chat_stream(
            messages=[{"role": "user", "content": "Write a file"}],
        ):
            deltas.append(delta)

        final = deltas[-1]
        assert final.is_final
        assert final.finish_reason == "tool_calls"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "write_file"
        assert final.tool_calls[0].arguments == {"path": "/tmp/f.txt"}
        assert final.tool_calls[0].id == "toolu_stream1"

    async def test_thinking_streaming(self, provider):
        """Thinking deltas yield StreamDelta(reasoning_content=...)."""
        events = [
            FakeContentBlockStartEvent(
                content_block=FakeThinkingBlock(type="thinking"),
                index=0,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeThinkingDelta(thinking="Let me think..."),
                index=0,
            ),
            FakeContentBlockStopEvent(index=0),
            FakeContentBlockStartEvent(
                content_block=FakeTextBlock(type="text"),
                index=1,
            ),
            FakeContentBlockDeltaEvent(
                delta=FakeTextDelta(text="Answer"),
                index=1,
            ),
            FakeContentBlockStopEvent(index=1),
            FakeMessageDeltaEvent(
                delta=FakeMessageDeltaStop(stop_reason="end_turn"),
                usage=FakeMessageDeltaUsage(output_tokens=30),
            ),
        ]

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = lambda self: self
        mock_stream._events = iter(events)

        async def _anext(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

        mock_stream.__anext__ = _anext
        provider._client.messages.stream.return_value = mock_stream

        deltas = []
        async for delta in provider.chat_stream(
            messages=[{"role": "user", "content": "Think hard"}],
        ):
            deltas.append(delta)

        thinking_deltas = [d for d in deltas if d.reasoning_content]
        assert len(thinking_deltas) == 1
        assert thinking_deltas[0].reasoning_content == "Let me think..."

        text_deltas = [d for d in deltas if d.content and not d.is_final]
        assert len(text_deltas) == 1
        assert text_deltas[0].content == "Answer"

    async def test_stream_error_handling(self, provider):
        """Errors during streaming yield error StreamDelta."""
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=RuntimeError("Stream broke"))
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        provider._client.messages.stream.return_value = mock_stream

        deltas = []
        async for delta in provider.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
        ):
            deltas.append(delta)

        assert len(deltas) == 1
        assert deltas[0].is_final
        assert deltas[0].finish_reason == "error"
        assert deltas[0].error_type == "RuntimeError"

    async def test_stream_error_content_is_truncated(self, provider):
        """Long stream errors are bounded before being returned to callers."""
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=RuntimeError("x" * 510))
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        provider._client.messages.stream.return_value = mock_stream

        deltas = []
        async for delta in provider.chat_stream(messages=[{"role": "user", "content": "Hi"}]):
            deltas.append(delta)

        assert deltas[0].content == f"Error: {'x' * 500}..."


# ===========================================================================
# TestGetDefaultModel
# ===========================================================================


class TestGetDefaultModel:
    def test_returns_configured_model(self, provider):
        assert provider.get_default_model() == "claude-sonnet-4-20250514"

    def test_supports_streaming_true(self, provider):
        assert provider.supports_streaming is True
