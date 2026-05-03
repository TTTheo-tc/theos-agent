"""In-process pub/sub for real-time SSE dashboard events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


class _SubscriptionIterator:
    """Async iterator that cleans up when garbage collected or explicitly closed."""

    def __init__(self, bus: UIEventBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._bus._subscribers.append(self._queue)
        self._active = True

    def __aiter__(self) -> _SubscriptionIterator:
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
        """Ensure cleanup even if aclose() is not called."""
        self._cleanup()


class UIEventBus:
    """Fan-out event bus: one publisher, many SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        """Push an event to all active subscribers."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Return async iterator for events. Cleans up on exit."""
        return _SubscriptionIterator(self)
