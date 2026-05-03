"""Two-tier response cache -- LRU memory + SQLite warm storage."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from src.store.database import Database

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_cache (
    cache_key   TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    hit_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rc_accessed ON response_cache(accessed_at);
"""


class ResponseCache:
    def __init__(
        self,
        db: Database,
        *,
        max_memory: int = 256,
        ttl_seconds: int = 3600,
        max_db_entries: int = 5000,
    ):
        self._db = db
        self._hot: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._max_memory = max_memory
        self._ttl = ttl_seconds
        self._max_db_entries = max_db_entries
        self._ready = False

    async def ensure_table(self) -> None:
        if self._ready:
            return
        await self._db.executescript(_CACHE_SCHEMA)
        self._ready = True

    @staticmethod
    def make_key(model: str, system_prompt: str, user_message: str) -> str:
        raw = f"{model}|{system_prompt}|{user_message}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    async def get(self, key: str) -> str | None:
        await self.ensure_table()
        now = time.time()
        if key in self._hot:
            response, ts = self._hot[key]
            if now - ts < self._ttl:
                self._hot.move_to_end(key)
                return response
            else:
                del self._hot[key]
        row = await self._db.fetchone(
            "SELECT response, created_at FROM response_cache WHERE cache_key = ?",
            (key,),
        )
        if row:
            created = datetime.fromisoformat(str(row[1]))
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age < self._ttl:
                response = row[0]
                self._hot[key] = (response, now)
                self._evict_hot()
                await self._db.execute(
                    "UPDATE response_cache SET hit_count = hit_count + 1, accessed_at = ?"
                    " WHERE cache_key = ?",
                    (datetime.now(timezone.utc).isoformat(), key),
                )
                return response
            else:
                await self._db.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))
        return None

    async def put(self, key: str, model: str, response: str, token_count: int = 0) -> None:
        await self.ensure_table()
        now = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()
        self._hot[key] = (response, now)
        self._evict_hot()
        await self._db.execute(
            """INSERT INTO response_cache
               (cache_key, model, response, token_count, hit_count, created_at, accessed_at)
               VALUES (?, ?, ?, ?, 0, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
                   response = excluded.response, token_count = excluded.token_count,
                   created_at = excluded.created_at, accessed_at = excluded.accessed_at""",
            (key, model, response, token_count, now_iso, now_iso),
        )
        count_row = await self._db.fetchone("SELECT COUNT(*) FROM response_cache")
        if count_row and count_row[0] > self._max_db_entries:
            delete_n = count_row[0] - self._max_db_entries + self._max_db_entries // 5
            await self._db.execute(
                "DELETE FROM response_cache WHERE cache_key IN "
                "(SELECT cache_key FROM response_cache ORDER BY accessed_at ASC LIMIT ?)",
                (delete_n,),
            )

    async def stats(self) -> dict[str, Any]:
        await self.ensure_table()
        row = await self._db.fetchone(
            "SELECT COUNT(*), COALESCE(SUM(hit_count), 0),"
            " COALESCE(SUM(token_count), 0) FROM response_cache"
        )
        return {
            "hot_entries": len(self._hot),
            "warm_entries": row[0] if row else 0,
            "total_hits": row[1] if row else 0,
            "tokens_saved": row[2] if row else 0,
        }

    async def clear(self) -> None:
        self._hot.clear()
        await self._db.execute("DELETE FROM response_cache")

    def _evict_hot(self) -> None:
        while len(self._hot) > self._max_memory:
            self._hot.popitem(last=False)
