import asyncio
from miner.core.worker_manager import WorkerManager

# Create a worker manager instance
worker_manager = WorkerManager(num_workers=3, max_queue_size=10)

# Keep the lock for backward compatibility, but it's no longer the primary mechanism
miner_lock = asyncio.Lock() 