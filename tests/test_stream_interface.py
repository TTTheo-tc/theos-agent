import pytest

from src.providers.base import LLMProvider, LLMResponse, StreamDelta, ToolCallRequest


class FakeProvider(LLMProvider):
    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="hello", finish_reason="stop", usage={"total_tokens": 10})

    def get_default_model(self):
        return "fake/model"


def test_stream_delta_defaults():
    d = StreamDelta()
    assert d.content is None
    assert d.tool_calls == []
    assert d.is_final is False
    assert d.finish_reason is None


def test_supports_streaming_default_false():
    p = FakeProvider()
    assert p.supports_streaming is False


@pytest.mark.asyncio
async def test_chat_stream_fallback_yields_single_final_delta():
    p = FakeProvider()
    deltas = [d async for d in p.chat_stream(messages=[{"role": "user", "content": "hi"}])]
    assert len(deltas) == 1
    assert deltas[0].is_final is True
    assert deltas[0].content == "hello"
    assert deltas[0].finish_reason == "stop"
    assert deltas[0].usage == {"total_tokens": 10}


@pytest.mark.asyncio
async def test_chat_stream_fallback_preserves_tool_calls():
    class ToolProvider(LLMProvider):
        async def chat(self, messages, **kwargs):
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t1", name="search", arguments={"q": "test"})],
                finish_reason="stop",
            )

        def get_default_model(self):
            return "fake/model"

    p = ToolProvider()
    deltas = [d async for d in p.chat_stream(messages=[])]
    assert deltas[0].tool_calls[0].name == "search"


@pytest.mark.asyncio
async def test_chat_stream_fallback_preserves_error():
    class ErrorProvider(LLMProvider):
        async def chat(self, messages, **kwargs):
            return LLMResponse(content="Error: bad", finish_reason="error", error_type="ValueError")

        def get_default_model(self):
            return "fake/model"

    p = ErrorProvider()
    deltas = [d async for d in p.chat_stream(messages=[])]
    assert deltas[0].finish_reason == "error"
    assert deltas[0].error_type == "ValueError"
