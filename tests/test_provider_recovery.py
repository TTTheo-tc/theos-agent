"""Tests for src.providers.recovery and src.providers.recovery_provider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from src.providers.errors import FailureClass
from src.providers.recovery import RecoveryAction, decide_recovery

# ---------------------------------------------------------------------------
# decide_recovery unit tests (unchanged from original)
# ---------------------------------------------------------------------------


def test_ok_returns_none():
    assert decide_recovery(FailureClass.OK) is None


def test_auth_with_fallback_failover():
    assert (
        decide_recovery(FailureClass.AUTH, attempt=0, has_fallback=True) is RecoveryAction.FAILOVER
    )


def test_auth_no_fallback_stop():
    assert decide_recovery(FailureClass.AUTH, attempt=0, has_fallback=False) is RecoveryAction.STOP


def test_model_not_found_with_fallback_failover():
    assert (
        decide_recovery(FailureClass.MODEL_NOT_FOUND, attempt=0, has_fallback=True)
        is RecoveryAction.FAILOVER
    )


def test_model_not_found_no_fallback_stop():
    assert (
        decide_recovery(FailureClass.MODEL_NOT_FOUND, attempt=0, has_fallback=False)
        is RecoveryAction.STOP
    )


def test_context_exceeded_with_fallback_failover():
    assert (
        decide_recovery(FailureClass.CONTEXT_EXCEEDED, attempt=0, has_fallback=True)
        is RecoveryAction.FAILOVER
    )


def test_context_exceeded_no_fallback_stop():
    assert (
        decide_recovery(FailureClass.CONTEXT_EXCEEDED, attempt=0, has_fallback=False)
        is RecoveryAction.STOP
    )


def test_rate_limit_attempt_0_retry():
    assert decide_recovery(FailureClass.RATE_LIMIT, attempt=0) is RecoveryAction.RETRY


def test_rate_limit_attempt_2_with_fallback_failover():
    assert (
        decide_recovery(FailureClass.RATE_LIMIT, attempt=2, has_fallback=True)
        is RecoveryAction.FAILOVER
    )


def test_retryable_attempt_0_retry():
    assert decide_recovery(FailureClass.RETRYABLE, attempt=0) is RecoveryAction.RETRY


def test_retryable_attempt_2_with_fallback_failover():
    assert (
        decide_recovery(FailureClass.RETRYABLE, attempt=2, has_fallback=True)
        is RecoveryAction.FAILOVER
    )


def test_retryable_attempt_2_no_fallback_stop():
    assert (
        decide_recovery(FailureClass.RETRYABLE, attempt=2, has_fallback=False)
        is RecoveryAction.STOP
    )


def test_non_retryable_stop():
    assert decide_recovery(FailureClass.NON_RETRYABLE) is RecoveryAction.STOP


# ===========================================================================
# RecoveryProvider tests
# ===========================================================================


class FakeProvider(LLMProvider):
    """Test helper: yields canned responses in order and tracks calls."""

    def __init__(
        self,
        responses: list[LLMResponse | Exception],
        default_model: str = "fake/model-a",
    ):
        super().__init__()
        self._responses = list(responses)
        self._default_model = default_model
        self.call_count = 0
        self.last_model_used: str | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_model_used = model
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_default_model(self) -> str:
        return self._default_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OK_RESPONSE = LLMResponse(content="hello", finish_reason="stop")
RETRYABLE_RESPONSE = LLMResponse(
    content="Error calling LLM: internal server error", finish_reason="error"
)
AUTH_RESPONSE = LLMResponse(
    content="Error calling LLM: authentication failed", finish_reason="error"
)


def _make_recovery_provider(primary: LLMProvider, fallbacks: list[LLMProvider] | None = None):
    from src.providers.recovery_provider import RecoveryProvider

    return RecoveryProvider(primary, fallbacks or [])


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_returns_immediately():
    """Primary succeeds on first try -- no recovery needed."""
    primary = FakeProvider([OK_RESPONSE])
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert primary.call_count == 1


# ---------------------------------------------------------------------------
# Retryable response retries primary before failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_response_retries_primary_then_succeeds():
    """Retryable error response -> retries primary -> succeeds on retry."""
    primary = FakeProvider([RETRYABLE_RESPONSE, RETRYABLE_RESPONSE, OK_RESPONSE])
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert primary.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_retryable_response_exhausts_retries_then_failover():
    """Retryable error response -> exhausts retries -> fails over to fallback."""
    primary = FakeProvider([RETRYABLE_RESPONSE, RETRYABLE_RESPONSE, RETRYABLE_RESPONSE])
    fallback = FakeProvider([OK_RESPONSE], default_model="fallback/model-b")
    rp = _make_recovery_provider(primary, [fallback])
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# Retryable exception retries primary before failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_exception_retries_primary_then_succeeds():
    """Retryable exception -> retries primary -> succeeds on retry."""
    primary = FakeProvider(
        [RuntimeError("connection reset"), RuntimeError("connection reset"), OK_RESPONSE]
    )
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert primary.call_count == 3


@pytest.mark.asyncio
async def test_retryable_exception_exhausts_retries_then_failover():
    """Retryable exception -> exhausts retries -> fails over to fallback."""
    primary = FakeProvider(
        [
            RuntimeError("connection reset"),
            RuntimeError("connection reset"),
            RuntimeError("connection reset"),
        ]
    )
    fallback = FakeProvider([OK_RESPONSE], default_model="fallback/model-b")
    rp = _make_recovery_provider(primary, [fallback])
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# Auth response fails over to fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_response_fails_over_immediately():
    """Auth error response -> no retries -> immediate failover."""
    primary = FakeProvider([AUTH_RESPONSE])
    fallback = FakeProvider([OK_RESPONSE], default_model="fallback/model-b")
    rp = _make_recovery_provider(primary, [fallback])
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert primary.call_count == 1  # no retries for auth
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# Auth exception fails over to fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_exception_fails_over_immediately():
    """Auth exception -> no retries -> immediate failover."""
    primary = FakeProvider([Exception("HTTP 401: unauthorized")])
    fallback = FakeProvider([OK_RESPONSE], default_model="fallback/model-b")
    rp = _make_recovery_provider(primary, [fallback])
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert primary.call_count == 1
    assert fallback.call_count == 1


# ---------------------------------------------------------------------------
# No fallback available returns final error response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fallback_returns_error_response():
    """Auth error with no fallback -> returns the error response."""
    primary = FakeProvider([AUTH_RESPONSE])
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.finish_reason == "error"
    assert "authentication" in resp.content.lower()


@pytest.mark.asyncio
async def test_no_fallback_exception_returns_error_response():
    """Exception with no fallback -> returns synthetic error response."""
    primary = FakeProvider([RuntimeError("connection reset")] * 3)
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.finish_reason == "error"


@pytest.mark.asyncio
async def test_non_retryable_error_response_stops_without_retry_or_failover():
    """Swallowed non-retryable errors must not be retried or failed over."""
    primary = FakeProvider(
        [
            LLMResponse(
                content="Error calling LLM: bad argument",
                finish_reason="error",
                error_type="ValueError",
            )
        ]
    )
    fallback = FakeProvider([OK_RESPONSE], default_model="fallback/model-b")
    rp = _make_recovery_provider(primary, [fallback])
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.finish_reason == "error"
    assert primary.call_count == 1
    assert fallback.call_count == 0


# ---------------------------------------------------------------------------
# Tool calls pass through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_calls_pass_through():
    """Successful response with tool calls passes through unchanged."""
    tool_call = ToolCallRequest(id="tc1", name="read_file", arguments={"path": "/tmp"})
    tool_response = LLMResponse(content=None, tool_calls=[tool_call], finish_reason="tool_calls")
    primary = FakeProvider([tool_response])
    rp = _make_recovery_provider(primary)
    resp = await rp.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "read_file"


# ---------------------------------------------------------------------------
# Fallback receives its own default model (critical)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_uses_own_default_model():
    """When failing over, fallback must be called with its own default model, not primary's."""
    primary = FakeProvider([AUTH_RESPONSE], default_model="anthropic/claude-opus-4-5")
    fallback = FakeProvider([OK_RESPONSE], default_model="anthropic/claude-sonnet-4-6")
    rp = _make_recovery_provider(primary, [fallback])

    await rp.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="anthropic/claude-opus-4-5",
    )

    # The fallback must have been called with its own default model
    assert fallback.last_model_used == "anthropic/claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_fallback_chain_second_fallback_also_uses_own_model():
    """First fallback fails too -> second fallback uses its own model."""
    primary = FakeProvider([AUTH_RESPONSE], default_model="primary/model")
    fb1 = FakeProvider([AUTH_RESPONSE], default_model="fb1/model")
    fb2 = FakeProvider([OK_RESPONSE], default_model="fb2/model")
    rp = _make_recovery_provider(primary, [fb1, fb2])

    await rp.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="primary/model",
    )

    assert fb1.last_model_used == "fb1/model"
    assert fb2.last_model_used == "fb2/model"


# ---------------------------------------------------------------------------
# get_default_model delegates to primary
# ---------------------------------------------------------------------------


def test_get_default_model_delegates_to_primary():
    primary = FakeProvider([], default_model="anthropic/claude-opus-4-5")
    rp = _make_recovery_provider(primary)
    assert rp.get_default_model() == "anthropic/claude-opus-4-5"


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_returns_wrapped_provider_when_failover_configured():
    """make_provider wraps with RecoveryProvider when failover_models is set."""
    from src.config.schema import Config
    from src.providers.recovery_provider import RecoveryProvider

    config = Config()
    config.agents.defaults.failover_models = ["anthropic/claude-sonnet-4-6"]

    fake_primary = FakeProvider([OK_RESPONSE], default_model="anthropic/claude-opus-4-5")
    fake_fallback = FakeProvider([OK_RESPONSE], default_model="anthropic/claude-sonnet-4-6")

    with (
        patch("src.providers.factory._build_provider", return_value=fake_primary),
        patch("src.providers.factory.make_provider_for_model", return_value=fake_fallback),
    ):
        from src.providers.factory import make_provider

        provider = make_provider(config)
        assert isinstance(provider, RecoveryProvider)


@pytest.mark.asyncio
async def test_factory_returns_plain_provider_when_no_failover():
    """make_provider returns plain provider when failover_models is empty."""
    from src.config.schema import Config

    config = Config()
    assert config.agents.defaults.failover_models == []

    fake_primary = FakeProvider([OK_RESPONSE])

    with patch("src.providers.factory._build_provider", return_value=fake_primary):
        from src.providers.factory import make_provider

        provider = make_provider(config)
        assert not hasattr(provider, "_primary")  # Not a RecoveryProvider


# ---------------------------------------------------------------------------
# Exponential backoff with jitter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_calls_sleep_with_backoff():
    """Verify asyncio.sleep is called between retries."""
    err = LLMResponse(content="Error calling LLM: Connection reset", finish_reason="error")
    ok = LLMResponse(content="recovered", finish_reason="stop")
    primary = FakeProvider([err, ok], default_model="primary/model")
    rp = _make_recovery_provider(primary, [])

    with patch(
        "src.providers.recovery_provider.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        result = await rp.chat(messages=[{"role": "user", "content": "hi"}])

    assert result.content == "recovered"
    assert mock_sleep.call_count == 1
    delay = mock_sleep.call_args[0][0]
    assert 0.3 <= delay <= 0.7  # ~0.5s ± 20% jitter


@pytest.mark.asyncio
async def test_second_retry_delay_greater_than_first():
    """Verify exponential increase."""
    err = LLMResponse(content="Error calling LLM: timeout", finish_reason="error")
    ok = LLMResponse(content="ok", finish_reason="stop")
    primary = FakeProvider([err, err, ok], default_model="primary/model")
    rp = _make_recovery_provider(primary, [])

    delays = []

    async def capture_sleep(d):
        delays.append(d)

    with patch("src.providers.recovery_provider.asyncio.sleep", side_effect=capture_sleep):
        await rp.chat(messages=[{"role": "user", "content": "hi"}])

    assert len(delays) == 2
    assert delays[1] > delays[0]
