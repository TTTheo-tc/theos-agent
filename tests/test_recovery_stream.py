"""Tests for RecoveryProvider.chat_stream() with retry and failover."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.base import LLMProvider, LLMResponse, StreamDelta
from src.providers.recovery_provider import RecoveryProvider

# ---------------------------------------------------------------------------
# Fake streaming provider
# ---------------------------------------------------------------------------


class FakeStreamProvider(LLMProvider):
    """Test helper: yields canned StreamDelta sequences and tracks calls."""

    def __init__(
        self,
        deltas_list: list[list[StreamDelta]],
        default_model: str = "fake/model",
    ):
        super().__init__()
        self._deltas_list = list(deltas_list)
        self._default_model = default_model
        self.call_count = 0
        self.last_model_used: str | None = None
        self._supports_streaming = True

    @property
    def supports_streaming(self) -> bool:
        return self._supports_streaming

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content="fallback-chat", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> None:
        self.call_count += 1
        self.last_model_used = model
        for d in self._deltas_list.pop(0):
            yield d

    def get_default_model(self) -> str:
        return self._default_model


class RaisingStreamProvider(LLMProvider):
    """Provider that raises on the Nth delta (0-indexed)."""

    def __init__(
        self,
        deltas_before_raise: list[StreamDelta],
        exc: Exception,
        default_model: str = "raise/model",
    ):
        super().__init__()
        self._deltas_before = deltas_before_raise
        self._exc = exc
        self._default_model = default_model
        self.call_count = 0

    @property
    def supports_streaming(self) -> bool:
        return True

    async def chat(self, messages, **kw) -> LLMResponse:
        return LLMResponse(content="nope", finish_reason="error")

    async def chat_stream(self, messages, **kw):
        self.call_count += 1
        for d in self._deltas_before:
            yield d
        raise self._exc

    def get_default_model(self) -> str:
        return self._default_model


MSGS = [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# 1. Success passthrough — primary streams fine, all deltas forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_passthrough():
    deltas = [
        StreamDelta(content="Hello"),
        StreamDelta(content=" world"),
        StreamDelta(content="!", is_final=True, finish_reason="stop"),
    ]
    primary = FakeStreamProvider([deltas])
    rp = RecoveryProvider(primary, [])

    collected = []
    async for d in rp.chat_stream(messages=MSGS):
        collected.append(d)

    assert len(collected) == 3
    assert collected[0].content == "Hello"
    assert collected[1].content == " world"
    assert collected[2].is_final
    assert collected[2].finish_reason == "stop"
    assert primary.call_count == 1


# ---------------------------------------------------------------------------
# 2. Pre-delta error failover — primary yields error with no prior content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_delta_error_failover():
    """Primary yields an auth error delta (no content sent yet) -> failover."""
    error_delta = StreamDelta(
        content="authentication failed",
        is_final=True,
        finish_reason="error",
        error_type="AuthError",
    )
    success_deltas = [
        StreamDelta(content="OK"),
        StreamDelta(content="!", is_final=True, finish_reason="stop"),
    ]
    primary = FakeStreamProvider([[error_delta]])
    fallback = FakeStreamProvider([success_deltas], default_model="fallback/model")
    rp = RecoveryProvider(primary, [fallback])

    collected = []
    async for d in rp.chat_stream(messages=MSGS, model="primary/model"):
        collected.append(d)

    assert len(collected) == 2
    assert collected[0].content == "OK"
    assert collected[1].is_final
    assert primary.call_count == 1
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# 3. Pre-delta exception failover — primary raises before yielding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_delta_exception_failover():
    """Primary raises an auth exception before yielding -> failover."""
    success_deltas = [
        StreamDelta(content="recovered"),
        StreamDelta(is_final=True, finish_reason="stop"),
    ]
    primary = RaisingStreamProvider([], Exception("HTTP 401: unauthorized"))
    fallback = FakeStreamProvider([success_deltas], default_model="fallback/model")
    rp = RecoveryProvider(primary, [fallback])

    collected = []
    async for d in rp.chat_stream(messages=MSGS):
        collected.append(d)

    assert len(collected) == 2
    assert collected[0].content == "recovered"
    assert primary.call_count == 1
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# 4. Post-delta error stops — already committed, NO failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_delta_exception_no_failover():
    """Primary yields content then raises -> error delta, no failover."""
    deltas_before = [StreamDelta(content="partial")]
    primary = RaisingStreamProvider(
        deltas_before, RuntimeError("connection lost"), default_model="primary/model"
    )
    fallback = FakeStreamProvider(
        [[StreamDelta(content="nope", is_final=True, finish_reason="stop")]],
        default_model="fallback/model",
    )
    rp = RecoveryProvider(primary, [fallback])

    collected = []
    async for d in rp.chat_stream(messages=MSGS):
        collected.append(d)

    assert len(collected) == 2
    assert collected[0].content == "partial"
    assert collected[1].is_final
    assert collected[1].finish_reason == "error"
    assert "connection lost" in collected[1].content
    # Fallback must NOT have been called
    assert fallback.call_count == 0


@pytest.mark.asyncio
async def test_post_delta_error_delta_no_failover():
    """Primary yields content then an error final delta -> no failover."""
    deltas = [
        StreamDelta(content="some text"),
        StreamDelta(
            content="server error",
            is_final=True,
            finish_reason="error",
            error_type="ServerError",
        ),
    ]
    primary = FakeStreamProvider([deltas])
    fallback = FakeStreamProvider(
        [[StreamDelta(content="nope", is_final=True, finish_reason="stop")]],
        default_model="fallback/model",
    )
    rp = RecoveryProvider(primary, [fallback])

    collected = []
    async for d in rp.chat_stream(messages=MSGS):
        collected.append(d)

    # Both deltas should be yielded (content + error) since we already committed
    assert len(collected) == 2
    assert collected[0].content == "some text"
    assert collected[1].is_final
    assert collected[1].finish_reason == "error"
    assert fallback.call_count == 0


# ---------------------------------------------------------------------------
# 5. supports_streaming delegates to primary
# ---------------------------------------------------------------------------


def test_supports_streaming_delegates_true():
    primary = FakeStreamProvider([])
    primary._supports_streaming = True
    rp = RecoveryProvider(primary, [])
    assert rp.supports_streaming is True


def test_supports_streaming_delegates_false():
    primary = FakeStreamProvider([])
    primary._supports_streaming = False
    rp = RecoveryProvider(primary, [])
    assert rp.supports_streaming is False


# ---------------------------------------------------------------------------
# 6. Fallback uses its own model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_uses_own_model():
    """When failing over in streaming, fallback receives its own default model."""
    error_delta = StreamDelta(
        content="authentication failed",
        is_final=True,
        finish_reason="error",
        error_type="AuthError",
    )
    success_deltas = [
        StreamDelta(content="ok", is_final=True, finish_reason="stop"),
    ]
    primary = FakeStreamProvider([[error_delta]], default_model="primary/model-a")
    fallback = FakeStreamProvider([success_deltas], default_model="fallback/model-b")
    rp = RecoveryProvider(primary, [fallback])

    collected = []
    async for d in rp.chat_stream(messages=MSGS, model="primary/model-a"):
        collected.append(d)

    assert primary.last_model_used == "primary/model-a"
    assert fallback.last_model_used == "fallback/model-b"


# ---------------------------------------------------------------------------
# 7. Pre-delta retryable error retries with backoff then succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_delta_retryable_error_retries_then_succeeds():
    """Retryable stream error -> retry -> succeed on second attempt."""
    error_delta = StreamDelta(
        content="internal server error",
        is_final=True,
        finish_reason="error",
        error_type="ServerError",
    )
    success_deltas = [
        StreamDelta(content="ok"),
        StreamDelta(is_final=True, finish_reason="stop"),
    ]
    primary = FakeStreamProvider([[error_delta], success_deltas])
    rp = RecoveryProvider(primary, [])

    with patch(
        "src.providers.recovery_provider.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        collected = []
        async for d in rp.chat_stream(messages=MSGS):
            collected.append(d)

    assert len(collected) == 2
    assert collected[0].content == "ok"
    assert primary.call_count == 2
    assert mock_sleep.call_count == 1
    delay = mock_sleep.call_args[0][0]
    assert 0.3 <= delay <= 0.7  # ~0.5s +/- jitter


# ---------------------------------------------------------------------------
# 8. Pre-delta exception retries with backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_delta_exception_retries_with_backoff():
    """Retryable exception -> retry -> succeed."""

    success_deltas = [
        StreamDelta(content="recovered"),
        StreamDelta(is_final=True, finish_reason="stop"),
    ]

    class RetryThenSucceed(LLMProvider):
        def __init__(self):
            super().__init__()
            self._attempts = 0

        @property
        def supports_streaming(self):
            return True

        async def chat(self, messages, **kw):
            return LLMResponse(content="", finish_reason="error")

        async def chat_stream(self, messages, **kw):
            self._attempts += 1
            if self._attempts <= 1:
                raise RuntimeError("connection reset")
            for d in success_deltas:
                yield d

        def get_default_model(self):
            return "retry/model"

    primary = RetryThenSucceed()
    rp = RecoveryProvider(primary, [])

    with patch("src.providers.recovery_provider.asyncio.sleep", new_callable=AsyncMock):
        collected = []
        async for d in rp.chat_stream(messages=MSGS):
            collected.append(d)

    assert len(collected) == 2
    assert collected[0].content == "recovered"
    assert primary._attempts == 2
