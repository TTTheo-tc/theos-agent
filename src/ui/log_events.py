"""Separate event bus for log streaming — independent of dashboard UIEventBus."""

from __future__ import annotations

import asyncio
from typing import Any

from src.safety.leak_detector import scrub_credentials


class _LogSubscriptionIterator:
    """Async iterator with explicit cleanup on disconnect."""

    def __init__(self, bus: LogEventBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._bus._subscribers.append(self._queue)
        self._active = True

    def __aiter__(self) -> _LogSubscriptionIterator:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._active:
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            self._cleanup()
            raise StopAsyncIteration
        return event

    def _cleanup(self) -> None:
        if self._active:
            self._active = False
            if self._queue in self._bus._subscribers:
                self._bus._subscribers.remove(self._queue)

    async def aclose(self) -> None:
        self._cleanup()

    def __del__(self) -> None:
        self._cleanup()


class LogEventBus:
    """Fan-out bus for real-time log lines. Sanitizes before delivery."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []

    async def publish(self, log_entry: dict[str, Any]) -> None:
        sanitized = {**log_entry}
        if "text" in sanitized:
            sanitized["text"] = scrub_credentials(sanitized["text"])
        if "record" in sanitized and "message" in sanitized["record"]:
            sanitized["record"]["message"] = scrub_credentials(sanitized["record"]["message"])
        for q in list(self._subscribers):
            try:
                q.put_nowait(sanitized)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> _LogSubscriptionIterator:
        """Return async iterator for log events. Cleans up on exit."""
        return _LogSubscriptionIterator(self)
