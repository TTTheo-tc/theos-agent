"""Recovery policy for LLM provider failures."""

from __future__ import annotations

from enum import Enum

from src.providers.errors import FailureClass

MAX_RETRIES = 2

# Failure classes that are non-retryable regardless of attempt count or fallback.
_IMMEDIATE_STOP = frozenset({FailureClass.NON_RETRYABLE})

# Failure classes that bypass retry logic and go straight to failover/stop.
_NO_RETRY = frozenset(
    {
        FailureClass.AUTH,
        FailureClass.MODEL_NOT_FOUND,
        FailureClass.CONTEXT_EXCEEDED,
    }
)

# Failure classes that allow retries before failover/stop.
_RETRYABLE = frozenset({FailureClass.RATE_LIMIT, FailureClass.RETRYABLE})


class RecoveryAction(Enum):
    """Action to take after an LLM provider failure."""

    RETRY = "retry"
    FAILOVER = "failover"
    STOP = "stop"


def decide_recovery(
    failure: FailureClass,
    *,
    attempt: int = 0,
    has_fallback: bool = False,
) -> RecoveryAction | None:
    """Return the recovery action for a given failure, attempt count, and fallback availability.

    Returns ``None`` when no recovery is needed (``FailureClass.OK``).
    """
    if failure is FailureClass.OK:
        return None

    if failure in _IMMEDIATE_STOP:
        return RecoveryAction.STOP

    if failure in _NO_RETRY:
        return RecoveryAction.FAILOVER if has_fallback else RecoveryAction.STOP

    # _RETRYABLE failures: retry up to MAX_RETRIES, then failover or stop.
    if failure in _RETRYABLE:
        if attempt < MAX_RETRIES:
            return RecoveryAction.RETRY
        return RecoveryAction.FAILOVER if has_fallback else RecoveryAction.STOP

    # Unknown failure class — treat as non-retryable.
    return RecoveryAction.STOP
