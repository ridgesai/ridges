import asyncio
import time
from functools import wraps
from typing import Callable, Any

import utils.logger as logger


def retrieval_cache(expire: int):
    def decorator(func: Callable):
        function_lock = asyncio.Lock()
        cache_key_to_locks: dict[str, asyncio.Lock] = {}
        cache_key_to_values: dict[str, Any] = {}
        cache_key_to_expirations: dict[str, int] = {}

        async def get_cache_key_lock(cache_key: str) -> asyncio.Lock:
            async with function_lock:
                if cache_key not in cache_key_to_locks:
                    cache_key_to_locks[cache_key] = asyncio.Lock()
                return cache_key_to_locks[cache_key]

        async def refresh(func: Callable, args: tuple, kwargs: dict, lock: asyncio.Lock, cache_key: str) -> None:
            async with lock:
                try:
                    value = await func(*args, **kwargs)
                    cache_key_to_values[cache_key] = value
                    cache_key_to_expirations[cache_key] = time.time() + expire
                except Exception as e:
                    logger.error(f"Error refreshing cache for {cache_key}: {e}")

        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            cache_key = func.__name__ + "_" + str(args) + "_" + str(kwargs)
            now = time.time()

            # need locks per cache key
            lock = await get_cache_key_lock(cache_key)

            cached_value = cache_key_to_values.get(cache_key, None)
            ttl = cache_key_to_expirations.get(cache_key, None)

            if cached_value is not None:
                if ttl <= now:
                    # avoiding cache stampede during refresh
                    if not lock.locked():
                        asyncio.create_task(refresh(func, args, kwargs, lock, cache_key))
                return cached_value
            
            # deal with cold-misses race conditions
            async with lock:
                cached_value = cache_key_to_values.get(cache_key, None)
                ttl = cache_key_to_expirations.get(cache_key, None)

                if cached_value is not None:
                    return cached_value

                value = await func(*args, **kwargs)
                cache_key_to_values[cache_key] = value
                cache_key_to_expirations[cache_key] = time.time() + expire
                return value

        return wrapper
    return decorator

# race conditions & notes:

# creating multiple locks for the same cache key - use global function lock to create cache key locks
# two cold misses, both create new value and timestamp - use cache key lock to check and create
# when cache expires, use lock to refresh query and others should return stale data