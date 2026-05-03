"""Failure classifier for LLM provider responses and exceptions."""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.base import LLMResponse


class FailureClass(Enum):
    """Classification of LLM provider failures."""

    OK = "ok"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT_EXCEEDED = "context_exceeded"
    MODEL_NOT_FOUND = "model_not_found"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


# ---------------------------------------------------------------------------
# Regex patterns applied to lowercased error text (ordered: most specific first)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[FailureClass, re.Pattern[str]]] = [
    (
        FailureClass.AUTH,
        re.compile(
            r"authentication|unauthorized|invalid.{0,10}(api.?key|token)|"
            r"\b401\b|permission.denied",
        ),
    ),
    (
        FailureClass.RATE_LIMIT,
        re.compile(r"rate.limit|429|too.many.requests|quota.exceeded"),
    ),
    (
        FailureClass.CONTEXT_EXCEEDED,
        re.compile(
            r"context.length|context_length|token.limit|"
            r"maximum.token|prompt.too.long|input.too.long",
        ),
    ),
    (
        FailureClass.MODEL_NOT_FOUND,
        re.compile(r"model.not.found|model.does.not.exist|model.not.available"),
    ),
]

# Exception types that are programming errors — never retry.
_NON_RETRYABLE_TYPES = (ValueError, TypeError)
_NON_RETRYABLE_TYPE_NAMES = frozenset(t.__name__ for t in _NON_RETRYABLE_TYPES)


def _classify_text(text: str) -> FailureClass:
    """Classify a lowercased error string against known patterns."""
    lower = text.lower()
    for failure_class, pattern in _PATTERNS:
        if pattern.search(lower):
            return failure_class
    return FailureClass.RETRYABLE


def classify_failure(
    *,
    response: LLMResponse | None = None,
    exception: BaseException | None = None,
) -> FailureClass:
    """Classify an LLM provider failure.

    Accepts keyword-only arguments:
    - ``response``: an ``LLMResponse`` (only ``finish_reason="error"`` is a failure).
    - ``exception``: any ``BaseException``.

    When both are supplied the exception takes precedence.
    Raises ``ValueError`` if neither argument is provided.
    """
    if response is None and exception is None:
        raise ValueError("classify_failure requires at least one of: response, exception")

    if exception is not None:
        if isinstance(exception, _NON_RETRYABLE_TYPES):
            return FailureClass.NON_RETRYABLE
        return _classify_text(str(exception))

    # response path
    assert response is not None
    if response.finish_reason != "error":
        return FailureClass.OK
    if response.error_type in _NON_RETRYABLE_TYPE_NAMES:
        return FailureClass.NON_RETRYABLE

    return _classify_text(response.content or "")
