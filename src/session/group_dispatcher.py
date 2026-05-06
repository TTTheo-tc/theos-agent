"""Per-group message queue dispatcher (Phase 3).

Ensures messages from different groups are processed concurrently
without blocking each other, while messages within the same group
remain serialized.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable

from loguru import logger

from src.bus.events import InboundMessage


class PerGroupDispatcher:
    """Dispatch inbound messages to per-group queues with independent workers.

    Each group (identified by ``channel:chat_id``) gets its own asyncio queue
    and worker task. Groups process concurrently; messages within a group are
    serialized (FIFO). Workers self-terminate after 60 s of inactivity.

    Usage::

        dispatcher = PerGroupDispatcher(process_fn)
        await dispatcher.dispatch(msg)   # from the bus consumer loop
        dispatcher.cancel_all()          # on shutdown
    """

    WORKER_IDLE_TIMEOUT = 60.0  # seconds before idle worker exits

    def __init__(
        self,
        process_fn: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        """
        Args:
            process_fn: Coroutine called for each message. Should handle its
                        own exception catching — errors here will be logged but
                        won't crash the dispatcher.
        """
        self._process = process_fn
        self._queues: defaultdict[str, asyncio.Queue[InboundMessage]] = defaultdict(asyncio.Queue)
        self._workers: dict[str, asyncio.Task[None]] = {}

    async def dispatch(self, msg: InboundMessage) -> None:
        """Enqueue *msg* for its group and start a worker if none is running."""
        group_id = msg.session_key
        await self._queues[group_id].put(msg)

        if group_id not in self._workers or self._workers[group_id].done():
            self._workers[group_id] = asyncio.create_task(
                self._worker(group_id), name=f"group-worker:{group_id}"
            )

    def cancel_all(self) -> None:
        """Cancel all running worker tasks (call on shutdown)."""
        for task in self._workers.values():
            if not task.done():
                task.cancel()
        self._workers.clear()

    def cancel_group(self, group_id: str) -> bool:
        """Cancel the running worker and clear the queue for a specific group."""
        self._drain_queue(group_id)

        task = self._workers.get(group_id)
        cancelled = bool(task and not task.done())
        if cancelled:
            task.cancel()

        return cancelled

    def _drain_queue(self, group_id: str) -> None:
        queue = self._queues.get(group_id)
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                return

    async def _worker(self, group_id: str) -> None:
        """Drain the queue for *group_id* until it is idle for WORKER_IDLE_TIMEOUT."""
        logger.debug("Worker started for group {}", group_id)
        queue = self._queues[group_id]
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=self.WORKER_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    # No messages for a while — exit so we don't leak tasks
                    logger.debug("Worker idle timeout for group {}, exiting", group_id)
                    break

                try:
                    await self._process(msg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.opt(exception=True).warning(
                        "PerGroupDispatcher: error processing message for {}", group_id
                    )
                finally:
                    queue.task_done()
        finally:
            self._workers.pop(group_id, None)
            logger.debug("Worker stopped for group {}", group_id)
