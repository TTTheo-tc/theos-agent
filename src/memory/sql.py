"""Short-term memory store — SQLite buffer/audit tier between immediate deque and long-term markdown.

This is a buffer/audit store, **not** the primary consolidation input.
Consolidation reads conversation history from ``Session.messages``;
``mark_consolidated()`` flags rows as processed for bookkeeping, but the
authoritative history lives in the Session, not here.

This module is also **not** a retrieval source.  ``MemoryRecallService``
reads from markdown (MEMORY.md) and structured memory (JSON); it does
not query ``memory_short_term``.  If SQLite data is ever surfaced for
recall or indexing, it should go through a normalization seam rather
than being read directly into prompt context.
"""

from __future__ import annotations

import json
from typing import Any

from src.memory.json_utils import coerce_json_object
from src.store.database import Database

_MESSAGE_COLUMNS = "id, session_key, role, content, timestamp, metadata, consolidated"


class ShortTermMemoryStore:
    """Read/write short-term conversation messages in ``memory_short_term``.

    This store is a buffer/audit layer, not the primary consolidation input.
    ``mark_consolidated()`` flags rows as processed, but the authoritative
    conversation history is ``Session.messages``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def write_messages(self, session_key: str, messages: list[dict[str, Any]]) -> None:
        """Batch-insert messages (typically flushed from the immediate deque)."""
        if not messages:
            return
        await self._db.execute_many(
            "INSERT INTO memory_short_term (session_key, role, content, timestamp, metadata)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    session_key,
                    m.get("role", "unknown"),
                    m.get("content", ""),
                    m.get("timestamp", ""),
                    json.dumps(m.get("metadata", {})),
                )
                for m in messages
            ],
        )

    async def get_recent(self, session_key: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent messages for a session."""
        rows = await self._db.fetchall(
            f"SELECT {_MESSAGE_COLUMNS}"
            " FROM memory_short_term WHERE session_key = ? ORDER BY id DESC LIMIT ?",
            (session_key, limit),
        )
        return [self._row_to_dict(r) for r in reversed(rows)]

    async def get_unconsolidated(
        self, session_key: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return messages not yet consolidated, oldest first."""
        rows = await self._db.fetchall(
            f"SELECT {_MESSAGE_COLUMNS}"
            " FROM memory_short_term WHERE session_key = ? AND consolidated = 0 ORDER BY id LIMIT ?",
            (session_key, limit),
        )
        return [self._row_to_dict(r) for r in rows]

    async def mark_consolidated(self, session_key: str, up_to_id: int) -> None:
        """Mark all messages up to (and including) *up_to_id* as consolidated.

        This is a bookkeeping operation: it records that the consolidation
        pipeline has processed these rows.  The authoritative consolidation
        input is ``Session.messages``, not these SQLite rows.
        """
        await self._db.execute(
            "UPDATE memory_short_term SET consolidated = 1 WHERE session_key = ? AND id <= ?",
            (session_key, up_to_id),
        )

    async def count_unconsolidated(self, session_key: str) -> int:
        """Return the number of unconsolidated messages for a session."""
        row = await self._db.fetchone(
            "SELECT COUNT(*) FROM memory_short_term WHERE session_key = ? AND consolidated = 0",
            (session_key,),
        )
        return row[0] if row else 0

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "session_key": row[1],
            "role": row[2],
            "content": row[3],
            "timestamp": row[4],
            "metadata": _metadata_dict(row[5]),
            "consolidated": bool(row[6]),
        }


def _metadata_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    return coerce_json_object(value)
