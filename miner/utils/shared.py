import asyncio
from miner.core.worker_manager import WorkerManager

# Limit miner to a single concurrent SWE-agent process and a one-item waiting queue
worker_manager = WorkerManager(num_workers=1, max_queue_size=1)

# Keep the lock for backward compatibility, but it's no longer the primary mechanism
miner_lock = asyncio.Lock() 