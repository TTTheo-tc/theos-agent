"""Three-tier memory management: immediate queue -> short-term SQLite -> long-term MEMORY.md.

Tier 2 (SQLite) is a short-term buffer and audit layer.  It captures
conversation messages for observability and post-hoc analysis but is
**not** the primary input for consolidation.  Consolidation reads from
``Session.messages``; marking SQLite rows as consolidated is a
bookkeeping operation that tracks which rows have been processed, not a
guarantee that SQLite is the archive source.

The tier pipeline (immediate queue -> SQLite) feeds into consolidation
and audit, **not** directly into retrieval.  ``MemoryRecallService``
retrieves from markdown (MEMORY.md) and structured memory (JSON); it
does not query the SQLite tier.  If SQLite data is ever used for recall
or indexing, it must go through a normalization seam before reaching
prompt context so raw SQLite rows are never surfaced directly.

Owns: immediate queue, flush to SQLite.
Does NOT own: consolidation logic (MemoryConsolidationService),
markdown writes (MemoryStore), retrieval (MemoryRecallService).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.config.schema import OrchestratorConfig
    from src.memory.sql import ShortTermMemoryStore
    from src.store.database import Database


class MemoryTierManager:
    """Manages three-tier memory: immediate buffer -> SQLite short-term -> file-based long-term.

    The SQLite short-term tier (Tier 2) is a buffer / audit layer.
    Consolidation reads from ``Session.messages``, not from SQLite.
    Marking rows as consolidated via ``ShortTermMemoryStore.mark_consolidated()``
    is a bookkeeping operation, not a guarantee that SQLite is the archive source.
    """

    def __init__(
        self,
        workspace: Path,
        orchestrator_config: OrchestratorConfig | None = None,
    ) -> None:
        self._workspace = workspace
        self._config = orchestrator_config
        self._db: Database | None = None
        self._short_term_store: ShortTermMemoryStore | None = None
        self._immediate_queues: dict[str, list[dict[str, Any]]] = {}
        self._flush_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def short_term_store(self) -> ShortTermMemoryStore | None:
        return self._short_term_store

    @property
    def enabled(self) -> bool:
        return bool(self._config and self._config.memory_tiers.enabled)

    @property
    def immediate_queue_size(self) -> int:
        if self._config:
            return self._config.memory_tiers.immediate_queue_size
        return 50

    async def ensure_db(self) -> None:
        """Lazy-init SQLite for three-tier memory when enabled."""
        if self._db is not None:
            return
        if not self._config or not self._config.memory_tiers.enabled:
            return
        from src.memory.sql import ShortTermMemoryStore
        from src.store.database import Database

        db_path = self._workspace / self._config.event_store.db_name
        self._db = Database(db_path)
        await self._db.connect()
        self._short_term_store = ShortTermMemoryStore(self._db)
        logger.info("Three-tier memory DB connected: {}", db_path)

    async def flush_immediate(self, session_key: str) -> None:
        """Flush the immediate queue to the short-term SQLite store."""
        if not self._short_term_store:
            return
        try:
            while True:
                queue = self._immediate_queues.get(session_key)
                if not queue:
                    return

                batch_size = len(queue)
                batch = list(queue)
                await self._short_term_store.write_messages(session_key, batch)

                # Only remove the entries we actually wrote — new entries may
                # have been appended by buffer_entry() during the await above.
                del queue[:batch_size]

                if not queue:
                    return
        except Exception:
            logger.opt(exception=True).warning("Short-term memory flush failed for {}", session_key)
        finally:
            current = asyncio.current_task()
            if self._flush_tasks.get(session_key) is current:
                self._flush_tasks.pop(session_key, None)
                queue = self._immediate_queues.get(session_key)
                if queue and len(queue) >= self.immediate_queue_size:
                    self._schedule_flush(session_key)

    def _schedule_flush(self, session_key: str) -> None:
        """Ensure at most one background flush task is active per session."""
        task = self._flush_tasks.get(session_key)
        if task is not None and not task.done():
            return
        self._flush_tasks[session_key] = asyncio.create_task(self.flush_immediate(session_key))

    def buffer_entry(self, session_key: str, entry: dict[str, Any]) -> None:
        """Buffer a message entry into the immediate queue, flushing if threshold reached."""
        if self._short_term_store is None:
            return
        queue = self._immediate_queues.setdefault(session_key, [])
        queue.append(entry)
        if len(queue) >= self.immediate_queue_size:
            self._schedule_flush(session_key)
