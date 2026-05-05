"""Tests for src/providers/errors.py — failure classifier."""

import pytest

from src.providers.base import LLMResponse
from src.providers.errors import FailureClass, classify_failure, short_error_message

# ---------------------------------------------------------------------------
# Response path
# ---------------------------------------------------------------------------


class TestClassifyResponse:
    def test_ok_on_stop_finish_reason(self):
        resp = LLMResponse(content="Hello", finish_reason="stop")
        assert classify_failure(response=resp) == FailureClass.OK

    def test_ok_on_length_finish_reason(self):
        resp = LLMResponse(content="...", finish_reason="length")
        assert classify_failure(response=resp) == FailureClass.OK

    def test_ok_on_tool_calls_finish_reason(self):
        resp = LLMResponse(content=None, finish_reason="tool_calls")
        assert classify_failure(response=resp) == FailureClass.OK

    def test_auth_error_response(self):
        resp = LLMResponse(
            content="Error calling LLM (authentication failed): invalid api key",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.AUTH

    def test_auth_unauthorized_response(self):
        resp = LLMResponse(
            content="Error calling LLM: 401 unauthorized",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.AUTH

    def test_rate_limit_response(self):
        resp = LLMResponse(
            content="Error calling LLM: rate limit exceeded",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.RATE_LIMIT

    def test_rate_limit_429_response(self):
        resp = LLMResponse(
            content="Error calling LLM: 429 Too Many Requests",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.RATE_LIMIT

    def test_rate_limit_quota_response(self):
        resp = LLMResponse(
            content="Error calling LLM: quota exceeded for this model",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.RATE_LIMIT

    def test_context_exceeded_response(self):
        resp = LLMResponse(
            content="Error calling LLM: context length exceeded",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.CONTEXT_EXCEEDED

    def test_context_token_limit_response(self):
        resp = LLMResponse(
            content="Error calling LLM: maximum token limit reached",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.CONTEXT_EXCEEDED

    def test_model_not_found_response(self):
        resp = LLMResponse(
            content="Error calling LLM: model not found",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.MODEL_NOT_FOUND

    def test_model_not_available_response(self):
        resp = LLMResponse(
            content="Error calling LLM: model does not exist",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.MODEL_NOT_FOUND

    def test_retryable_unknown_error_response(self):
        resp = LLMResponse(
            content="Error calling LLM: internal server error",
            finish_reason="error",
        )
        assert classify_failure(response=resp) == FailureClass.RETRYABLE

    def test_retryable_on_none_content_error(self):
        resp = LLMResponse(content=None, finish_reason="error")
        assert classify_failure(response=resp) == FailureClass.RETRYABLE

    def test_non_retryable_error_type_on_response(self):
        resp = LLMResponse(content="Error calling LLM: bad argument", finish_reason="error")
        resp.error_type = "ValueError"
        assert classify_failure(response=resp) == FailureClass.NON_RETRYABLE


# ---------------------------------------------------------------------------
# Exception path
# ---------------------------------------------------------------------------


class TestClassifyException:
    def test_non_retryable_value_error(self):
        exc = ValueError("bad argument")
        assert classify_failure(exception=exc) == FailureClass.NON_RETRYABLE

    def test_non_retryable_type_error(self):
        exc = TypeError("wrong type")
        assert classify_failure(exception=exc) == FailureClass.NON_RETRYABLE

    def test_auth_exception(self):
        exc = PermissionError("authentication failed: invalid key")
        assert classify_failure(exception=exc) == FailureClass.AUTH

    def test_auth_401_in_message(self):
        exc = Exception("HTTP 401: unauthorized")
        assert classify_failure(exception=exc) == FailureClass.AUTH

    def test_rate_limit_exception(self):
        exc = Exception("rate limit exceeded, try again later")
        assert classify_failure(exception=exc) == FailureClass.RATE_LIMIT

    def test_rate_limit_429_exception(self):
        exc = Exception("429 Too Many Requests")
        assert classify_failure(exception=exc) == FailureClass.RATE_LIMIT

    def test_context_exceeded_exception(self):
        exc = Exception("context_length_exceeded: prompt too long")
        assert classify_failure(exception=exc) == FailureClass.CONTEXT_EXCEEDED

    def test_context_token_limit_exception(self):
        exc = Exception("maximum token limit reached for this model")
        assert classify_failure(exception=exc) == FailureClass.CONTEXT_EXCEEDED

    def test_model_not_found_exception(self):
        exc = Exception("model not found: gpt-99")
        assert classify_failure(exception=exc) == FailureClass.MODEL_NOT_FOUND

    def test_model_not_available_exception(self):
        exc = Exception("The model does not exist or is not available")
        assert classify_failure(exception=exc) == FailureClass.MODEL_NOT_FOUND

    def test_retryable_generic_exception(self):
        exc = Exception("connection reset by peer")
        assert classify_failure(exception=exc) == FailureClass.RETRYABLE

    def test_retryable_runtime_error(self):
        exc = RuntimeError("temporary failure in name resolution")
        assert classify_failure(exception=exc) == FailureClass.RETRYABLE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_args_raises(self):
        with pytest.raises(ValueError, match="response.*exception"):
            classify_failure()

    def test_both_args_exception_takes_precedence(self):
        """When both are supplied the exception path is used."""
        resp = LLMResponse(content="ok", finish_reason="stop")
        exc = ValueError("bad")
        assert classify_failure(response=resp, exception=exc) == FailureClass.NON_RETRYABLE

    def test_short_error_message_truncates_long_exceptions(self):
        exc = RuntimeError("x" * 510)

        assert short_error_message(exc) == ("x" * 500) + "..."
