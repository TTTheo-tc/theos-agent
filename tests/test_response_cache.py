"""Tests for src/memory/response_cache.py — two-tier LRU + SQLite cache."""

from __future__ import annotations

import pytest

from src.memory.response_cache import ResponseCache, _utc_now_iso
from src.store.database import Database


@pytest.fixture
async def cache(tmp_path):
    db = Database(tmp_path / "cache.db")
    await db.connect()
    c = ResponseCache(db, max_memory=4, ttl_seconds=3600, max_db_entries=10)
    yield c
    await db.close()


# ---------------------------------------------------------------------------
# TestMakeKey
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_deterministic(self):
        k1 = ResponseCache.make_key("gpt-4", "system", "hello")
        k2 = ResponseCache.make_key("gpt-4", "system", "hello")
        assert k1 == k2

    def test_different_inputs(self):
        k1 = ResponseCache.make_key("gpt-4", "system", "hello")
        k2 = ResponseCache.make_key("gpt-4", "system", "world")
        assert k1 != k2

    def test_truncates_prompt(self):
        long_prompt = "x" * 10000
        k = ResponseCache.make_key("gpt-4", long_prompt, "msg")
        # Key should still be a 32-char hex string
        assert len(k) == 32


def test_utc_now_iso_is_timezone_aware() -> None:
    from datetime import datetime, timezone

    ts = datetime.fromisoformat(_utc_now_iso())

    assert ts.tzinfo is not None
    assert ts.utcoffset() == timezone.utc.utcoffset(ts)


# ---------------------------------------------------------------------------
# TestGetPut
# ---------------------------------------------------------------------------


class TestGetPut:
    async def test_put_and_get_hit(self, cache):
        key = ResponseCache.make_key("gpt-4", "sys", "hello")
        await cache.put(key, "gpt-4", "response text", token_count=42)
        result = await cache.get(key)
        assert result == "response text"

    async def test_miss(self, cache):
        result = await cache.get("nonexistent-key")
        assert result is None

    async def test_ttl_expiration(self, cache):
        """Entries expired in hot tier are removed from the hot dict."""
        key = ResponseCache.make_key("gpt-4", "sys", "expire-test")
        await cache.put(key, "gpt-4", "will expire", token_count=10)

        # Verify entry is in hot tier
        assert key in cache._hot

        # Manually expire the hot entry by backdating its timestamp
        resp, ts = cache._hot[key]
        cache._hot[key] = (resp, ts - 7200)  # 2 hours ago, TTL is 1 hour

        # Also remove the DB row so the get() doesn't hit the DB fallback path
        await cache._db.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))

        result = await cache.get(key)
        # Hot entry expired + DB row removed = miss
        assert result is None
        # Hot entry should have been cleaned up
        assert key not in cache._hot

    async def test_warm_hit_increments_hit_count(self, cache):
        key = ResponseCache.make_key("gpt-4", "sys", "warm-hit")
        await cache.put(key, "gpt-4", "warm response", token_count=10)
        cache._hot.clear()

        result = await cache.get(key)

        assert result == "warm response"
        stats = await cache.stats()
        assert stats["total_hits"] == 1

    async def test_expired_warm_entry_is_deleted(self, cache):
        key = ResponseCache.make_key("gpt-4", "sys", "warm-expire")
        await cache.put(key, "gpt-4", "old response", token_count=10)
        cache._hot.clear()
        await cache._db.execute(
            "UPDATE response_cache SET created_at = ? WHERE cache_key = ?",
            ("2000-01-01T00:00:00+00:00", key),
        )

        result = await cache.get(key)

        assert result is None
        row = await cache._db.fetchone(
            "SELECT cache_key FROM response_cache WHERE cache_key = ?",
            (key,),
        )
        assert row is None

    async def test_naive_warm_timestamp_is_treated_as_utc(self, cache):
        key = ResponseCache.make_key("gpt-4", "sys", "warm-naive")
        await cache.put(key, "gpt-4", "naive response", token_count=10)
        cache._hot.clear()
        await cache._db.execute(
            "UPDATE response_cache SET created_at = ? WHERE cache_key = ?",
            ("2999-01-01T00:00:00", key),
        )

        result = await cache.get(key)

        assert result == "naive response"


# ---------------------------------------------------------------------------
# TestEviction
# ---------------------------------------------------------------------------


class TestEviction:
    async def test_hot_tier_lru(self, cache):
        """Hot tier evicts oldest when max_memory (4) is exceeded."""
        for i in range(6):
            key = f"key-{i}"
            await cache.put(key, "model", f"response-{i}")

        # Only the last 4 should remain in hot tier
        assert len(cache._hot) == 4
        assert "key-0" not in cache._hot
        assert "key-1" not in cache._hot
        assert "key-5" in cache._hot

    async def test_warm_tier_eviction(self, cache):
        """DB evicts oldest entries when max_db_entries (10) is exceeded."""
        for i in range(15):
            key = f"warm-{i}"
            await cache.put(key, "model", f"response-{i}")

        stats = await cache.stats()
        # Should have trimmed to around max_db_entries (with 20% extra removed)
        assert stats["warm_entries"] <= 10


# ---------------------------------------------------------------------------
# TestStats
# ---------------------------------------------------------------------------


class TestStats:
    async def test_empty_stats(self, cache):
        s = await cache.stats()
        assert s["hot_entries"] == 0
        assert s["warm_entries"] == 0
        assert s["total_hits"] == 0
        assert s["tokens_saved"] == 0

    async def test_stats_after_hits(self, cache):
        key = "stats-key"
        await cache.put(key, "model", "resp", token_count=100)
        # Access twice from hot tier (doesn't increment DB hit_count)
        await cache.get(key)

        s = await cache.stats()
        assert s["hot_entries"] >= 1
        assert s["warm_entries"] >= 1
        assert s["tokens_saved"] == 100


# ---------------------------------------------------------------------------
# TestClear
# ---------------------------------------------------------------------------


class TestClear:
    async def test_clear_before_first_use_is_safe(self, tmp_path):
        db = Database(tmp_path / "cache.db")
        await db.connect()
        cache = ResponseCache(db)
        try:
            await cache.clear()
            stats = await cache.stats()
            assert stats["warm_entries"] == 0
        finally:
            await db.close()

    async def test_clear_empties_both_tiers(self, cache):
        for i in range(3):
            await cache.put(f"clear-{i}", "model", f"resp-{i}")

        assert len(cache._hot) > 0
        await cache.clear()
        assert len(cache._hot) == 0
        s = await cache.stats()
        assert s["warm_entries"] == 0
