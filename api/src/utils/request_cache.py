import time
import asyncio
from datetime import datetime
from functools import wraps
from typing import Any

def hourly_cache():
    def decorator(func):
        lock = asyncio.Lock()
        # Cache format: {"YYYY-MM-DD:HH": cached_result}
        hourly_bucket_cache: dict[str, Any] = {}

        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal hourly_bucket_cache

            cache_time = kwargs.pop('cache_time', time.time())
            hour_bucket = datetime.fromtimestamp(cache_time).strftime("%Y-%m-%d:%H")

            if hour_bucket in hourly_bucket_cache:
                return hourly_bucket_cache[hour_bucket]

            async with lock:
                result = await func(*args, **kwargs)
                hourly_bucket_cache[hour_bucket] = result
                return result

        return wrapper
    return decorator
