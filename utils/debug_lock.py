import time
import asyncio
import utils.logger as logger

DEBUG_LOCKS = {
    "acquired": set(),
    "locked": set(),
    "slow": set(),
}

class DebugLock:
    def __init__(self, lock: asyncio.Lock, label: str):
        self.lock = lock
        self.label = label

    async def __aenter__(self):
        start_time = time.time()
        logger.info(f"[DebugLock] {self.label}: Trying to acquire lock...")
        DEBUG_LOCKS["acquired"].add(self.label)
        await self.lock.acquire()
        elapsed = time.time() - start_time
        DEBUG_LOCKS["locked"].add(self.label)
        if elapsed > 5:
            DEBUG_LOCKS["slow"].add(self.label)
        logger.info(f"[DebugLock] {self.label}: Lock acquired after {elapsed:.2f} seconds")
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.lock.release()
        if self.label in DEBUG_LOCKS["locked"]:
            DEBUG_LOCKS["locked"].remove(self.label)
        if self.label in DEBUG_LOCKS["acquired"]:
            DEBUG_LOCKS["acquired"].remove(self.label)
        logger.info(f"[DebugLock] {self.label}: Lock released")