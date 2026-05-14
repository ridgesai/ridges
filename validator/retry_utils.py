import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

TRANSIENT_HTTP_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


async def retry_with_backoff(
    coro_fn: Callable[[], Coroutine[Any, Any, Any]],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 10.0,
) -> Any:
    """Call coro_fn(), retrying on transient HTTP errors with exponential backoff.

    Raises the last exception if all attempts are exhausted.
    Non-transient exceptions (e.g. 4xx HTTPStatusError) propagate immediately.
    """
    for attempt in range(max_attempts):
        try:
            return await coro_fn()
        except TRANSIENT_HTTP_ERRORS:
            if attempt >= max_attempts - 1:
                raise
            await asyncio.sleep(min(base_delay * (2**attempt), max_delay))
