
import os
import asyncio
import threading
from typing import List, Optional

from fiber.chain.interface import get_substrate
from fiber.chain.fetch_nodes import get_nodes_for_netuid

# Simple cache
_cache: Optional[List[str]] = None
_lock = threading.Lock()

def _fetch_hotkeys() -> List[str]:
    """Synchronous hotkey fetching."""
    substrate = get_substrate(
        subtensor_network=os.getenv("SUBTENSOR_NETWORK"), 
        subtensor_address=os.getenv("SUBTENSOR_ADDRESS")
    )
    active_nodes = get_nodes_for_netuid(substrate, int(os.getenv("NETUID")))
    return [node.hotkey for node in active_nodes]

async def update_cache_loop():
    """Background task that updates cache every minute."""
    global _cache
    while True:
        try:
            hotkeys = await asyncio.to_thread(_fetch_hotkeys)
            with _lock:
                _cache = hotkeys
        except Exception as e:
            print(f"Cache update error: {e}")
        await asyncio.sleep(60)

async def get_subnet_hotkeys():
    """Get hotkeys with caching."""
    if _cache is None:
        return await asyncio.to_thread(_fetch_hotkeys)
    return _cache.copy()
