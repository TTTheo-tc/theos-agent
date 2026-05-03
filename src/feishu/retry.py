"""Generic retry mechanism for Feishu API calls with error classification."""

from __future__ import annotations

import asyncio
import inspect
import random
import re
from enum import StrEnum
from typing import Any, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


class ErrorCategory(StrEnum):
    PERMANENT = "permanent"  # Invalid params, parse errors -- don't retry
    RATE_LIMITED = "rate_limited"  # 429, frequency limit -- retry after wait, not a failure
    RETRYABLE = "retryable"  # 5xx, network errors -- retry with backoff
    RETRYABLE_WITH_DELAY = "retryable_with_delay"  # Transient state -- retry after short delay


# Error codes that are permanent (won't resolve with retry)
_PERMANENT_CODES = {
    1770001,  # invalid parameter
    1770002,  # parse error
    1770004,  # block count exceeded
    1770032,  # forbidden (permission on block range)
    1062507,  # folder child limit exceeded
}

# Error codes that are retryable after a short delay (transient state)
_RETRYABLE_WITH_DELAY_CODES = {
    131006,  # wiki node not ready (race after creation)
}


def classify_error(error: Exception) -> ErrorCategory:
    """Classify a Feishu API error into retry categories."""
    err_str = str(error).lower()
    err_code = _extract_error_code(error)

    # Rate limiting patterns
    if err_code in (429, 99991400):
        return ErrorCategory.RATE_LIMITED
    if any(p in err_str for p in ("rate limit", "frequency limit", "too many request")):
        return ErrorCategory.RATE_LIMITED

    # Transient state -- retry after short delay
    if err_code in _RETRYABLE_WITH_DELAY_CODES:
        return ErrorCategory.RETRYABLE_WITH_DELAY

    # Permanent errors -- don't retry
    if err_code in _PERMANENT_CODES:
        return ErrorCategory.PERMANENT
    if any(
        p in err_str for p in ("invalid param", "parse error", "permission denied", "not found")
    ):
        return ErrorCategory.PERMANENT

    # Everything else is retryable
    return ErrorCategory.RETRYABLE


def _extract_error_code(error: Exception) -> int | None:
    """Extract numeric error code from various error types."""
    # FeishuAPIError and lark-oapi errors often have .code attribute
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return code
    # Try parsing from string representation
    match = re.search(r'"code"\s*:\s*(\d+)', str(error))
    if match:
        return int(match.group(1))
    return None


def _extract_rate_limit_reset(error: Exception) -> float | None:
    """Try to extract x-ogw-ratelimit-reset from error response."""
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        if headers:
            reset = headers.get("x-ogw-ratelimit-reset")
            if reset:
                try:
                    return float(reset)
                except (ValueError, TypeError):
                    pass
    return None


async def with_retry(
    fn: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    max_attempts: int = 10,
    max_backoff: float = 30.0,
    default_rate_limit_wait: float = 1.0,
    action: str = "",
    **kwargs: Any,
) -> T:
    """Execute fn with retry logic.

    - Permanent errors: raise immediately
    - Rate-limited: wait and retry (doesn't count toward max_retries)
    - Retryable: exponential backoff with full jitter

    Args:
        fn: The function to call (sync or async).
        max_retries: Max retries for retryable errors.
        max_attempts: Total max attempts including rate-limit retries.
        max_backoff: Max backoff seconds for exponential backoff.
        default_rate_limit_wait: Default wait when no reset header found.
        action: Description for logging.
    """
    retries = 0
    attempts = 0
    last_error: Exception | None = None

    while attempts < max_attempts:
        attempts += 1
        try:
            if inspect.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        except Exception as e:
            last_error = e
            category = classify_error(e)

            if category == ErrorCategory.PERMANENT:
                logger.warning("[Feishu retry] Permanent error ({}): {}", action, e)
                raise

            if category == ErrorCategory.RETRYABLE_WITH_DELAY:
                retries += 1
                if retries > max_retries:
                    raise
                wait = 0.5  # short fixed delay for transient state
                logger.info(
                    "[Feishu retry] Transient state ({}), waiting {:.1f}s: {}", action, wait, e
                )
                await asyncio.sleep(wait)
                continue

            if category == ErrorCategory.RATE_LIMITED:
                reset = _extract_rate_limit_reset(e)
                wait = reset if reset is not None else default_rate_limit_wait
                logger.info("[Feishu retry] Rate limited ({}), waiting {:.1f}s", action, wait)
                await asyncio.sleep(wait)
                continue  # doesn't count toward retries

            # Retryable
            retries += 1
            if retries > max_retries:
                logger.warning(
                    "[Feishu retry] Max retries ({}) exceeded for {}",
                    max_retries,
                    action,
                )
                raise

            backoff = random.uniform(0, min(2**retries, max_backoff))
            logger.info(
                "[Feishu retry] Retryable error ({}), attempt {}/{}, backoff {:.1f}s: {}",
                action,
                retries,
                max_retries,
                backoff,
                e,
            )
            await asyncio.sleep(backoff)

    raise last_error or RuntimeError(f"Max attempts ({max_attempts}) exceeded for {action}")
