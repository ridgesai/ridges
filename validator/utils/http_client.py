import httpx
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

# Global shared client to prevent connection pool issues
_shared_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

@asynccontextmanager
async def get_shared_client(timeout: float = 300.0):
    """Get a shared HTTP client with proper connection limits."""
    global _shared_client
    
    async with _client_lock:
        if _shared_client is None:
            _shared_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        yield _shared_client

async def cleanup_shared_client():
    """Clean up the shared client. Call during shutdown."""
    global _shared_client
    if _shared_client:
        await _shared_client.aclose()
        _shared_client = None 