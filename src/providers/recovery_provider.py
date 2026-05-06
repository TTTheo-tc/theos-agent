"""Recovery-aware provider wrapper with retry and failover support."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from typing import Any

from src.providers.base import LLMProvider, LLMResponse, StreamDelta
from src.providers.errors import classify_failure
from src.providers.recovery import RecoveryAction, decide_recovery

logger = logging.getLogger(__name__)

_BACKOFF_INITIAL = 0.5
_BACKOFF_FACTOR = 2.0
_BACKOFF_CAP = 8.0
_BACKOFF_JITTER = 0.2  # ±20%


def _backoff_delay(attempt: int) -> float:
    delay = min(_BACKOFF_INITIAL * (_BACKOFF_FACTOR ** (attempt - 1)), _BACKOFF_CAP)
    jitter = delay * _BACKOFF_JITTER * (2 * random.random() - 1)
    return delay + jitter


def _error_delta_from_exception(exc: Exception) -> StreamDelta:
    return StreamDelta(
        content=f"Error: {exc}",
        is_final=True,
        finish_reason="error",
        error_type=type(exc).__name__,
    )


def _error_response_from_exception(exc: Exception) -> LLMResponse:
    return LLMResponse(
        content=f"Error calling LLM: {exc}",
        finish_reason="error",
        error_type=type(exc).__name__,
    )


async def _sleep_before_retry(provider: LLMProvider, attempt: int) -> None:
    delay = _backoff_delay(attempt)
    logger.debug(
        "Retrying provider %s (attempt %d, delay %.2fs)",
        provider.get_default_model(),
        attempt,
        delay,
    )
    await asyncio.sleep(delay)


class RecoveryProvider(LLMProvider):
    """Wraps a primary provider with retry logic and ordered fallback providers.

    On failure the primary is retried (for retryable errors) and then each
    fallback is tried in order.  When calling a fallback the ``model`` kwarg
    is overridden with that fallback's own default model so it doesn't
    accidentally receive the primary's model identifier.
    """

    def __init__(self, primary: LLMProvider, fallbacks: list[LLMProvider]) -> None:
        super().__init__()
        self._primary = primary
        self._fallbacks = fallbacks

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    @property
    def supports_streaming(self) -> bool:
        return self._primary.supports_streaming

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream with retry/failover.

        Before any content is yielded, errors trigger normal recovery logic
        (retry with backoff, then failover).  Once content has been yielded to
        the caller we are committed to the current provider — errors yield a
        final error delta and stop.
        """
        providers = [self._primary] + self._fallbacks

        for idx, provider in enumerate(providers):
            use_model = model if provider is self._primary else provider.get_default_model()
            has_fallback = idx < len(providers) - 1
            attempt = 0
            action: RecoveryAction | None = None

            while True:
                has_yielded = False
                action = None
                try:
                    async for delta in provider.chat_stream(
                        messages=messages,
                        tools=tools,
                        model=use_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ):
                        # Error final delta before any actionable output was sent
                        if delta.is_final and delta.finish_reason == "error" and not has_yielded:
                            failure = classify_failure(
                                response=LLMResponse(
                                    content=delta.content,
                                    finish_reason="error",
                                    error_type=delta.error_type,
                                )
                            )
                            action = decide_recovery(
                                failure,
                                attempt=attempt,
                                has_fallback=has_fallback,
                            )
                            if action is RecoveryAction.RETRY:
                                attempt += 1
                                delay = _backoff_delay(attempt)
                                await asyncio.sleep(delay)
                                break  # retry inner while
                            elif action is RecoveryAction.FAILOVER:
                                break  # next provider
                            else:
                                # STOP or None — surface the error
                                yield delta
                                return

                        if not delta.is_final and (delta.content or delta.tool_ready):
                            has_yielded = True
                        yield delta

                        if delta.is_final:
                            return

                except Exception as exc:
                    if has_yielded:
                        yield _error_delta_from_exception(exc)
                        return
                    failure = classify_failure(exception=exc)
                    action = decide_recovery(
                        failure,
                        attempt=attempt,
                        has_fallback=has_fallback,
                    )
                    if action is RecoveryAction.RETRY:
                        attempt += 1
                        delay = _backoff_delay(attempt)
                        await asyncio.sleep(delay)
                        continue  # retry inner while
                    elif action is RecoveryAction.FAILOVER:
                        break  # next provider
                    else:
                        yield _error_delta_from_exception(exc)
                        return
                else:
                    # for-loop exhausted without break — check action
                    if action is RecoveryAction.RETRY:
                        continue  # retry inner while
                    elif action is RecoveryAction.FAILOVER:
                        break  # next provider
                    # Normal completion (no error delta seen), shouldn't happen
                    # since is_final should have triggered return, but be safe.
                    return

            # Broke out of inner while — action determines what to do
            if action is RecoveryAction.FAILOVER:
                logger.info(
                    "Stream failing over to provider %d (%s)",
                    idx + 1,
                    providers[idx + 1].get_default_model() if idx + 1 < len(providers) else "none",
                )
                continue  # next provider in outer for
            # If we broke for RETRY that's handled by inner while continue
            # If we got here unexpectedly, stop.
            return

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        has_fallback = len(self._fallbacks) > 0

        # 1. Try primary (with retries)
        response, should_failover = await self._try_with_retries(
            self._primary,
            has_fallback=has_fallback,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if not should_failover:
            return response

        # 2. Try each fallback in order
        for i, fb in enumerate(self._fallbacks):
            remaining = len(self._fallbacks) - i - 1
            logger.info("Failing over to fallback %d (%s)", i, fb.get_default_model())
            fb_response, should_failover = await self._try_with_retries(
                fb,
                has_fallback=remaining > 0,
                messages=messages,
                tools=tools,
                model=fb.get_default_model(),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if not should_failover:
                return fb_response
            response = fb_response  # keep last error for final return

        return response

    def get_default_model(self) -> str:
        return self._primary.get_default_model()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _try_with_retries(
        self,
        provider: LLMProvider,
        *,
        has_fallback: bool,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
    ) -> tuple[LLMResponse, bool]:
        """Try a single provider with retry logic.

        Returns ``(response, should_failover)``.
        """
        attempt = 0
        response: LLMResponse | None = None

        while True:
            try:
                response = await provider.chat(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                failure = classify_failure(response=response)
            except Exception as exc:
                response = _error_response_from_exception(exc)
                failure = classify_failure(exception=exc)

            action = decide_recovery(failure, attempt=attempt, has_fallback=has_fallback)

            if action is None or action is RecoveryAction.STOP:
                return response, False
            if action is RecoveryAction.FAILOVER:
                return response, True
            # action is RETRY
            attempt += 1
            await _sleep_before_retry(provider, attempt)
