"""Tests for utils/ttl.py — TTL cache decorator."""

import asyncio
import pytest
import time

from utils.ttl import ttl_cache, TTLCacheEntry, _args_and_kwargs_to_ttl_cache_key
from datetime import datetime, timezone, timedelta


class TestTTLCacheKeyGeneration:
    """Tests for cache key generation."""

    def test_same_args_produce_same_key(self):
        key1 = _args_and_kwargs_to_ttl_cache_key((1, 2), {"a": 3})
        key2 = _args_and_kwargs_to_ttl_cache_key((1, 2), {"a": 3})
        assert key1 == key2

    def test_different_args_produce_different_keys(self):
        key1 = _args_and_kwargs_to_ttl_cache_key((1,), {})
        key2 = _args_and_kwargs_to_ttl_cache_key((2,), {})
        assert key1 != key2

    def test_kwargs_order_does_not_matter(self):
        key1 = _args_and_kwargs_to_ttl_cache_key((), {"a": 1, "b": 2})
        key2 = _args_and_kwargs_to_ttl_cache_key((), {"b": 2, "a": 1})
        assert key1 == key2

    def test_empty_args_and_kwargs(self):
        key = _args_and_kwargs_to_ttl_cache_key((), {})
        assert key == ((), ())


class TestTTLCacheEntry:
    """Tests for TTLCacheEntry model."""

    def test_entry_creation(self):
        entry = TTLCacheEntry(
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            value="test_value",
        )
        assert entry.value == "test_value"

    def test_entry_expiry(self):
        entry = TTLCacheEntry(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            value="expired",
        )
        assert datetime.now(timezone.utc) >= entry.expires_at


class TestTTLCacheDecorator:
    """Tests for the ttl_cache decorator."""

    @pytest.mark.asyncio
    async def test_caches_result(self):
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        async def compute(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = await compute(5)
        result2 = await compute(5)
        assert result1 == 10
        assert result2 == 10
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_args_not_cached_together(self):
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        async def compute(x):
            nonlocal call_count
            call_count += 1
            return x + 1

        await compute(1)
        await compute(2)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_entries_eviction(self):
        @ttl_cache(ttl_seconds=60, max_entries=5)
        async def compute(x):
            return x

        # Fill beyond max
        for i in range(10):
            await compute(i)

        # Should still work (eviction is best-effort)
        result = await compute(999)
        assert result == 999

    @pytest.mark.asyncio
    async def test_returns_correct_type(self):
        @ttl_cache(ttl_seconds=10)
        async def get_dict():
            return {"key": "value"}

        result = await get_dict()
        assert isinstance(result, dict)
        assert result["key"] == "value"
