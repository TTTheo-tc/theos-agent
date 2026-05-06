"""Async message queue for decoupled channel-agent communication."""

import asyncio
from contextlib import suppress
from typing import TypeVar

from loguru import logger

from src.bus.events import InboundMessage, OutboundMessage

_MAX_QUEUE_SIZE = 1000
T = TypeVar("T")


class MessageBus:
    """Async message bus with backpressure."""

    def __init__(
        self,
        max_inbound: int = _MAX_QUEUE_SIZE,
        max_outbound: int = _MAX_QUEUE_SIZE,
    ) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_inbound)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=max_outbound)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self._publish_with_drop_oldest(self.inbound, msg, "Inbound")

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self._publish_with_drop_oldest(self.outbound, msg, "Outbound")

    async def _publish_with_drop_oldest(
        self,
        queue: asyncio.Queue[T],
        msg: T,
        label: str,
    ) -> None:
        try:
            queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("{} queue full ({}), dropping oldest message", label, queue.qsize())
            self._drop_oldest(queue)
            await queue.put(msg)

    @staticmethod
    def _drop_oldest(queue: asyncio.Queue[T]) -> None:
        with suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()
