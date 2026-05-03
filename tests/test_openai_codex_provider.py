import pytest

from src.providers.base import LLMResponse
from src.providers.openai_codex_provider import OpenAICodexProvider, _consume_sse


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
