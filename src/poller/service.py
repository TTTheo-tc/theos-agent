"""PollerService — manages lifecycle of all registered pollers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

from src.poller.base import BasePoller, PollerEvent

if TYPE_CHECKING:
    from src.bus.queue import MessageBus


class PollerService:
    """Manages multiple BasePoller instances.

    Each poller runs in its own asyncio task.  When a poller emits a
    ``PollerEvent``, the service injects an ``InboundMessage`` into the
    ``MessageBus`` so the agent loop processes it like any user message.
    """

    def __init__(
        self,
        bus: MessageBus,
        on_event: Callable[[PollerEvent], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.bus = bus
        self._on_event = on_event or self._default_on_event
        self._pollers: list[BasePoller] = []
        self._tasks: list[asyncio.Task[None]] = []

    def register(self, poller: BasePoller) -> None:
        """Register a poller to be started with the service."""
        self._pollers.append(poller)
        logger.info("Poller [{}] registered (interval={}s)", poller.name, poller.interval_s)

    async def start(self) -> None:
        """Start all registered pollers."""
        self._tasks = [task for task in self._tasks if not task.done()]
        if self._tasks:
            logger.warning("PollerService already started")
            return
        if not self._pollers:
            logger.debug("No pollers registered")
            return
        for poller in self._pollers:
            task = asyncio.create_task(
                poller.run_loop(self._on_event),
                name=f"poller.{poller.name}",
            )
            self._tasks.append(task)
        logger.info("PollerService started ({} pollers)", len(self._pollers))

    async def stop(self) -> None:
        """Stop all pollers."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("PollerService stopped")

    async def _default_on_event(self, event: PollerEvent) -> None:
        """Default handler: inject event into the message bus as an InboundMessage."""
        msg = self._message_from_event(event)
        await self.bus.publish_inbound(msg)
        logger.info("Poller [{}] → bus: {}", event.poller_name, event.message[:120])

    @staticmethod
    def _message_from_event(event: PollerEvent):
        from src.bus.events import InboundMessage

        return InboundMessage(
            channel="poller",
            sender_id=f"poller:{event.poller_name}",
            chat_id="poller",
            content=event.message,
            metadata={"poller_name": event.poller_name, **event.metadata},
            sender_is_owner=True,  # pollers are system-initiated, treat as owner
        )
