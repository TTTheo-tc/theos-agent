import pytest

from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_publish_and_consume_messages():
    bus = MessageBus()
    inbound = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="hello")
    outbound = OutboundMessage(channel="cli", chat_id="c1", content="hi")

    await bus.publish_inbound(inbound)
    await bus.publish_outbound(outbound)

    assert await bus.consume_inbound() == inbound
    assert await bus.consume_outbound() == outbound


@pytest.mark.asyncio
async def test_inbound_backpressure_drops_oldest_message():
    bus = MessageBus(max_inbound=1)
    first = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="first")
    second = InboundMessage(channel="cli", sender_id="u1", chat_id="c1", content="second")

    await bus.publish_inbound(first)
    await bus.publish_inbound(second)

    assert bus.inbound_size == 1
    assert await bus.consume_inbound() == second


@pytest.mark.asyncio
async def test_outbound_backpressure_drops_oldest_message():
    bus = MessageBus(max_outbound=1)
    first = OutboundMessage(channel="cli", chat_id="c1", content="first")
    second = OutboundMessage(channel="cli", chat_id="c1", content="second")

    await bus.publish_outbound(first)
    await bus.publish_outbound(second)

    assert bus.outbound_size == 1
    assert await bus.consume_outbound() == second
