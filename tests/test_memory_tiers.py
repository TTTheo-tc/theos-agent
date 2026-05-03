from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.memory.tiers import MemoryTierManager


class _BlockingShortTermStore:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.first_call_started = asyncio.Event()
        self._release_first = asyncio.Event()

    async def write_messages(self, session_key: str, batch: list[dict]) -> None:
        self.calls.append(list(batch))
        if len(self.calls) == 1:
            self.first_call_started.set()
            await self._release_first.wait()

    def release_first_call(self) -> None:
        self._release_first.set()


async def test_memory_tier_flush_is_single_flight_and_preserves_new_entries():
    cfg = SimpleNamespace(
        memory_tiers=SimpleNamespace(enabled=True, immediate_queue_size=2),
        event_store=SimpleNamespace(db_name="theos.db"),
    )
    manager = MemoryTierManager(Path("."), cfg)
    store = _BlockingShortTermStore()
    manager._short_term_store = store

    manager.buffer_entry("s", {"id": 1})
    manager.buffer_entry("s", {"id": 2})
    await store.first_call_started.wait()

    manager.buffer_entry("s", {"id": 3})
    manager.buffer_entry("s", {"id": 4})

    store.release_first_call()

    for _ in range(50):
        if "s" not in manager._flush_tasks:
            break
        await asyncio.sleep(0)

    assert store.calls == [
        [{"id": 1}, {"id": 2}],
        [{"id": 3}, {"id": 4}],
    ]
    assert manager._immediate_queues["s"] == []
