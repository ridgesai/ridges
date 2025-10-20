import time
import asyncio
import utils.logger as logger



DEBUG_LOCKS = {
    "waiting": [],
    "locked": [],
    "slow": []
}

DEBUG_LOCKS_LOCK = asyncio.Lock()



class DebugLock:
    def __init__(self, lock: asyncio.Lock, label: str):
        self.lock = lock
        self.label = label
        self.waiting_at = None
        self.acquired_at = None

    async def __aenter__(self):
        self.waiting_at = time.time()
        logger.info(f"[DebugLock] {self.label}: Trying to acquire lock...")
        waiting_entry = {
            "label": self.label,
            "waiting_at": self.waiting_at
        }
        async with DEBUG_LOCKS_LOCK:
            DEBUG_LOCKS["waiting"].append(waiting_entry)
        await self.lock.acquire()
        self.acquired_at = time.time()
        acquired_entry = {
            "label": self.label,
            "waiting_at": self.waiting_at,
            "acquired_at": self.acquired_at
        }
        async with DEBUG_LOCKS_LOCK:
            DEBUG_LOCKS["waiting"] = [x for x in DEBUG_LOCKS["waiting"] if not (x["label"] == self.label and x["waiting_at"] == self.waiting_at)]
            DEBUG_LOCKS["locked"].append(acquired_entry)
        elapsed = self.acquired_at - self.waiting_at
        logger.info(f"[DebugLock] {self.label}: Lock acquired after waiting for {elapsed:.2f} seconds")
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.lock.release()
        self.released_at = time.time()
        elapsed = self.released_at - self.acquired_at
        slow_entry = {
            "label": self.label,
            "waiting_at": self.waiting_at,
            "acquired_at": self.acquired_at,
            "released_at": self.released_at
        }
        async with DEBUG_LOCKS_LOCK:
            DEBUG_LOCKS["locked"] = [x for x in DEBUG_LOCKS["locked"] if not (x["label"] == self.label and x["waiting_at"] == self.waiting_at)]
            if elapsed > 5:
                DEBUG_LOCKS["slow"].append(slow_entry)
        logger.info(f"[DebugLock] {self.label}: Lock released after being locked for {elapsed:.2f} seconds")



def get_debug_lock_info():
    now = time.time()

    waiting_info = []
    for entry in DEBUG_LOCKS["waiting"]:
        seconds_waiting = now - entry["waiting_at"]
        waiting_info.append(f"{entry["label"]} - {seconds_waiting:.2f} s")

    locked_info = []
    for entry in DEBUG_LOCKS["locked"]:
        seconds_locked = now - entry["acquired_at"]
        locked_info.append(f"{entry["label"]} - {seconds_locked:.2f} s")

    slow_info = []
    for entry in DEBUG_LOCKS["slow"]:
        seconds_locked = entry["released_at"] - entry["acquired_at"]
        slow_info.append(f"{entry["label"]} - {seconds_locked:.2f} s")

    return {
        "waiting": waiting_info,
        "locked": locked_info,
        "slow": slow_info
    }