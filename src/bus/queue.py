"""Async message queue for decoupled channel-agent communication."""

import asyncio

from loguru import logger

from src.bus.events import InboundMessage, OutboundMessage

_MAX_QUEUE_SIZE = 1000


class MessageBus:
    """Async message bus with backpressure."""

    def __init__(self, max_inbound: int = _MAX_QUEUE_SIZE, max_outbound: int = _MAX_QUEUE_SIZE):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_inbound)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=max_outbound)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        try:
            self.inbound.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("Inbound queue full ({}), dropping oldest message", self.inbound.qsize())
            try:
                self.inbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        try:
            self.outbound.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning(
                "Outbound queue full ({}), dropping oldest message", self.outbound.qsize()
            )
            try:
                self.outbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self.outbound.qsize()
