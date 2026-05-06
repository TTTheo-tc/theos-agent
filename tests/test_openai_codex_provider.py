import json

import pytest

from src.providers.base import LLMResponse
from src.providers.openai_codex_provider import OpenAICodexProvider, _consume_sse, _iter_sse


@pytest.mark.asyncio
async def test_consume_sse_surfaces_error_message():
    class _FakeResponse:
        async def aiter_lines(self):
            yield 'data: {"type":"error","error":{"message":"token expired"}}'
            yield ""

    with pytest.raises(RuntimeError, match="token expired"):
        await _consume_sse(_FakeResponse())


@pytest.mark.asyncio
async def test_consume_sse_extracts_text_from_output_item_done_message():
    class _FakeResponse:
        async def aiter_lines(self):
            yield (
                'data: {"type":"response.output_item.done","item":{"type":"message","content":[{"type":"output_text","text":"hello world"}]}}'
            )
            yield ""
            yield 'data: {"type":"response.completed","response":{"status":"completed"}}'
            yield ""

    content, tool_calls, finish_reason, usage = await _consume_sse(_FakeResponse())

    assert content == "hello world"
    assert tool_calls == []
    assert finish_reason == "stop"
    assert usage == {}


@pytest.mark.asyncio
async def test_consume_sse_extracts_text_from_completed_output_fallback():
    class _FakeResponse:
        async def aiter_lines(self):
            yield (
                'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"final text"}]}]}}'
            )
            yield ""

    content, tool_calls, finish_reason, usage = await _consume_sse(_FakeResponse())

    assert content == "final text"
    assert tool_calls == []
    assert finish_reason == "stop"
    assert usage == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"path":"/tmp/a.txt"}', {"path": "/tmp/a.txt"}),
        ('["not", "an", "object"]', {"raw": '["not", "an", "object"]'}),
        ("not json", {"raw": "not json"}),
    ],
)
async def test_consume_sse_function_call_arguments_are_objects(arguments, expected):
    class _FakeResponse:
        async def aiter_lines(self):
            event = {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "id": "fc_1",
                    "name": "read_file",
                    "arguments": arguments,
                },
            }
            yield f"data: {json.dumps(event)}"
            yield ""
            yield 'data: {"type":"response.completed","response":{"status":"completed"}}'
            yield ""

    _, tool_calls, _, _ = await _consume_sse(_FakeResponse())

    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_1|fc_1"
    assert tool_calls[0].name == "read_file"
    assert tool_calls[0].arguments == expected


@pytest.mark.asyncio
async def test_consume_sse_streamed_function_call_arguments():
    class _FakeResponse:
        async def aiter_lines(self):
            events = [
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_1",
                        "id": "fc_1",
                        "name": "read_file",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "delta": '{"path":',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "delta": '"/tmp/a.txt"}',
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_1",
                        "id": "fc_1",
                    },
                },
                {"type": "response.completed", "response": {"status": "completed"}},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}"
                yield ""

    _, tool_calls, _, _ = await _consume_sse(_FakeResponse())

    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_1|fc_1"
    assert tool_calls[0].name == "read_file"
    assert tool_calls[0].arguments == {"path": "/tmp/a.txt"}


@pytest.mark.asyncio
async def test_consume_sse_extracts_usage_from_completed_response():
    class _FakeResponse:
        async def aiter_lines(self):
            yield (
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":7,"output_tokens":5}}}'
            )
            yield ""

    _, _, _, usage = await _consume_sse(_FakeResponse())

    assert usage == {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}


@pytest.mark.asyncio
async def test_consume_sse_preserves_explicit_total_tokens():
    class _FakeResponse:
        async def aiter_lines(self):
            yield (
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":7,"output_tokens":5,"total_tokens":99}}}'
            )
            yield ""

    _, _, _, usage = await _consume_sse(_FakeResponse())

    assert usage == {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 99}


@pytest.mark.asyncio
async def test_iter_sse_handles_multiline_data_and_done_marker():
    class _FakeResponse:
        async def aiter_lines(self):
            yield 'data: {"type":'
            yield 'data: "custom.event"}'
            yield ""
            yield "data: [DONE]"
            yield ""

    events = [event async for event in _iter_sse(_FakeResponse())]

    assert events == [{"type": "custom.event"}]


@pytest.mark.asyncio
async def test_chat_retries_once_on_transport_disconnect(monkeypatch):
    provider = OpenAICodexProvider()
    calls = {"count": 0}

    class _Token:
        account_id = "acct"
        access = "token"

    async def _fake_request(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise __import__("httpx").RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return "ok", [], "stop", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    async def _fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("src.providers.openai_codex_provider.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr("src.providers.openai_codex_provider.get_codex_token", lambda: _Token())
    monkeypatch.setattr("src.providers.openai_codex_provider._request_codex", _fake_request)

    resp = await provider.chat([{"role": "user", "content": "hello"}])

    assert isinstance(resp, LLMResponse)
    assert resp.content == "ok"
    assert resp.finish_reason == "stop"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_returns_friendly_message_after_transport_disconnect(monkeypatch):
    provider = OpenAICodexProvider()

    class _Token:
        account_id = "acct"
        access = "token"

    async def _fake_request(*args, **kwargs):
        raise __import__("httpx").RemoteProtocolError(
            "Server disconnected without sending a response."
        )

    async def _fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("src.providers.openai_codex_provider.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr("src.providers.openai_codex_provider.get_codex_token", lambda: _Token())
    monkeypatch.setattr("src.providers.openai_codex_provider._request_codex", _fake_request)

    resp = await provider.chat([{"role": "user", "content": "hello"}])

    assert resp.finish_reason == "error"
    assert resp.content == (
        "Error calling Codex: connection dropped before the response completed. Please retry."
    )
