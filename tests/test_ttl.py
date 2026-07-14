import asyncio

import pytest

from utils.ttl import ttl_cache


@pytest.mark.anyio
async def test_cache_clear_forces_recalculation() -> None:
    current_value = 1

    @ttl_cache(ttl_seconds=60)
    async def cached_value() -> int:
        return current_value

    assert await cached_value() == 1
    current_value = 2
    assert await cached_value() == 1

    cached_value.cache_clear()

    assert await cached_value() == 2


@pytest.mark.anyio
async def test_cache_clear_does_not_restore_in_flight_stale_value() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    @ttl_cache(ttl_seconds=60)
    async def cached_value() -> int:
        nonlocal call_count
        call_count += 1
        value = call_count
        started.set()
        await release.wait()
        return value

    first_call = asyncio.create_task(cached_value())
    await started.wait()
    cached_value.cache_clear()
    release.set()

    assert await first_call == 1
    assert await cached_value() == 2
