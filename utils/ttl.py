import asyncio
import utils.logger as logger

from functools import wraps
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timezone,timedelta
from typing import Any, Dict, Tuple, Callable, TypeAlias



TTLCacheKey: TypeAlias = Tuple[Any, ...]

def _args_and_kwargs_to_ttl_cache_key(args: Tuple, kwargs: Dict) -> TTLCacheKey:
    return (args, tuple(sorted(kwargs.items())))



class TTLCacheEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    expires_at: datetime
    value: Any
    


# NOTE ADAM: A robust TTL cache implementation. The implementation supports the following
#            behaviors:
#
#            1. The first request will hang while the first calculation occurs. It will then return
#               the newly calculated value.
#
#            2. Requests that occur while the first request is still ongoing will *not* attempt to
#               calculate again, they will simply wait for the first request to complete and return
#               the newly calculated value.
#
#            3. Requests that occur while there is fresh data in the cache will return the cached
#               data immediately.
#
#            4. Requests that occur while the data is stale will trigger a recalculation in the
#               background, but will continue to return the stale data until the recalculation is
#               complete.
#
#            5. Requests that occur while the data is stale while there is already an ongoing
#               recalculation will wait for the recalculation to complete and then return the newly
#               calculated value.
#
#            The effect of these behaviors is that there is a maximum of one recalculation occuring
#            at any given moment for a given key.
#
#
#
def ttl_cache(ttl_seconds: int, max_entries: int = 200):
    def decorator(func: Callable):
        cache: Dict[TTLCacheKey, TTLCacheEntry] = {}
        recalculating_locks: Dict[TTLCacheKey, asyncio.Lock] = {}


        def _evict_expired():
            """Remove all expired entries and their locks."""
            now = datetime.now(timezone.utc)
            expired = [k for k, v in cache.items() if now >= v.expires_at]
            for k in expired:
                del cache[k]
                recalculating_locks.pop(k, None)

            # Hard cap: if still over max_entries, drop oldest
            if len(cache) > max_entries:
                by_expiry = sorted(cache.items(), key=lambda kv: kv[1].expires_at)
                to_remove = len(cache) - max_entries
                for k, _ in by_expiry[:to_remove]:
                    del cache[k]
                    recalculating_locks.pop(k, None)


        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = _args_and_kwargs_to_ttl_cache_key(args, kwargs)

            # Periodically evict expired entries
            if len(cache) > max_entries // 2:
                _evict_expired()

            lock = recalculating_locks.setdefault(key, asyncio.Lock())

            if key in cache:
                cache_entry = cache[key]
                if datetime.now(timezone.utc) < cache_entry.expires_at:
                    logger.debug(f"[TTLCache] {func.__name__}(): Cache hit")
                    return cache_entry.value
                else:
                    if lock.locked():
                        logger.debug(f"[TTLCache] {func.__name__}(): Cache miss, already recalculating")
                        return cache_entry.value
                    else:
                        logger.debug(f"[TTLCache] {func.__name__}(): Cache miss, triggering recalculation")
                        asyncio.create_task(_recalculate(args, kwargs))
                        return cache_entry.value
            else:
                if lock.locked():
                    logger.debug(f"[TTLCache] {func.__name__}(): First request, already calculating, started waiting")
                    async with lock:
                        logger.debug(f"[TTLCache] {func.__name__}(): First request, already calculating, stopped waiting")
                        return cache[key].value
                else:
                    logger.debug(f"[TTLCache] {func.__name__}(): First request, triggering calculation")
                    await _recalculate(args, kwargs)
                    return cache[key].value



        async def _recalculate(args: Tuple, kwargs: Dict):
            key = _args_and_kwargs_to_ttl_cache_key(args, kwargs)

            async with recalculating_locks[key]:
                if key in cache and datetime.now(timezone.utc) < cache[key].expires_at:
                    return
                
                value = await func(*args, **kwargs)
                cache[key] = TTLCacheEntry(
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
                    value=value
                )
                logger.debug(f"[TTLCache] {func.__name__}(): Calculation completed")
        


        return wrapper
    


    return decorator
