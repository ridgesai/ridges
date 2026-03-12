# Tracks the number of platform-side inference errors per evaluation run.
# When the count exceeds a configured threshold the run is flagged as a
# platform error so the agent is not penalized unfairly.

import time

from uuid import UUID
from pydantic import BaseModel



ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS = 60 # 1 minute

class ErrorHashMapEntry(BaseModel):
    inference_errors: int
    last_accessed_at: float

class ErrorHashMap:
    def __init__(self):
        self.error_hash_map = {}
        self.last_cleanup_at = time.time()



    def _cleanup(self):
        now = time.time()
        if now - self.last_cleanup_at > ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS:
            self.error_hash_map = {k: v for k, v in self.error_hash_map.items() if now - v.last_accessed_at < ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS}
            self.last_cleanup_at = now



    def get_inference_errors(self, uuid: UUID) -> int:
        self._cleanup()

        if uuid in self.error_hash_map:
            self.error_hash_map[uuid].last_accessed_at = time.time()
            return self.error_hash_map[uuid].inference_errors
        else:
            return 0

    def add_inference_error(self, uuid: UUID):
        self._cleanup()

        if uuid in self.error_hash_map:
            entry = self.error_hash_map[uuid]
            entry.inference_errors += 1
            entry.last_accessed_at = time.time()
        else:
            self.error_hash_map[uuid] = ErrorHashMapEntry(inference_errors=1, last_accessed_at=time.time())
