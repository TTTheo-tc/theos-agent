from __future__ import annotations

import asyncio

from src.ui.events import UIEventBus


async def test_publish_subscribe():
    bus = UIEventBus()
    received = []

    async def consumer():
        async for event in bus.subscribe():
            received.append(event)
            if len(received) == 2:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)

    await bus.publish({"id": 1, "event_type": "test1"})
    await bus.publish({"id": 2, "event_type": "test2"})

    await asyncio.wait_for(task, timeout=1.0)
    assert len(received) == 2
    assert received[0]["id"] == 1
    assert received[1]["id"] == 2


async def test_multiple_subscribers():
    bus = UIEventBus()
    results_a = []
    results_b = []

    async def consumer_a():
        async for event in bus.subscribe():
            results_a.append(event)
            if len(results_a) == 1:
                break

    async def consumer_b():
        async for event in bus.subscribe():
            results_b.append(event)
            if len(results_b) == 1:
                break

    ta = asyncio.create_task(consumer_a())
    tb = asyncio.create_task(consumer_b())
    await asyncio.sleep(0.01)

    await bus.publish({"id": 1, "event_type": "test"})

    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=1.0)
    assert len(results_a) == 1
    assert len(results_b) == 1


async def test_unsubscribe_on_break():
    bus = UIEventBus()

    # Publish an event to let the subscriber receive it
    asyncio.create_task(_delayed_publish(bus, {"id": 1}))

    async for _ in bus.subscribe():
        # Break immediately after receiving first event
        break

    # Cleanup happens via __del__ when iterator goes out of scope
    assert len(bus._subscribers) == 0


async def _delayed_publish(bus: UIEventBus, event: dict[str, object]) -> None:
    """Publish an event after a small delay."""
    await asyncio.sleep(0.001)
    await bus.publish(event)
