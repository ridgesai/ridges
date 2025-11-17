import time
import asyncio
from functools import wraps

def hourly_cache(ttl: int = 3600):
    def decorator(func):
        last_value = None
        last_ts = 0.0
        lock = asyncio.Lock()

        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal last_value, last_ts
            now = time.time()

            # Fast path: still fresh
            if last_value is not None and now - last_ts < ttl:
                return last_value

            # Slow path: refresh under a lock
            async with lock:
                # Double-check inside lock
                now = time.time()
                if last_value is not None and now - last_ts < ttl:
                    return last_value

                result = await func(*args, **kwargs)
                last_value = result
                last_ts = now
                return result

        return wrapper
    return decorator
