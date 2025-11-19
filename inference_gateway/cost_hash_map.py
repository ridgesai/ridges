# NOTE ADAM: We used to use the database as the source of truth for determining
#            how expensive a given evaluation run is by summing the individual
#            costs of all inferences/embeddings associated with that evaluation
#            run. This is actually quite a slow query, so instead, since we
#            only have one inference gateway to begin with, the cost
#            information is tracked here in memory.

import time

from uuid import UUID
from pydantic import BaseModel



COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS = 60 # 1 minute

class CostHashMapEntry(BaseModel):
    cost: float
    last_accessed_at: float

class CostHashMap:
    def __init__(self):
        self.cost_hash_map = {}
        self.last_cleanup_at = time.time()



    def _cleanup(self):
        now = time.time()
        if now - self.last_cleanup_at > COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS:
            self.cost_hash_map = {k: v for k, v in self.cost_hash_map.items() if now - v.last_accessed_at < COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS}
            self.last_cleanup_at = now



    def get_cost(self, uuid: UUID) -> float:
        self._cleanup()

        if uuid in self.cost_hash_map:
            self.cost_hash_map[uuid].last_accessed_at = time.time()
            return self.cost_hash_map[uuid].cost
        else:
            # TODO ADAM: db
            return 0

    def add_cost(self, uuid: UUID, cost: float):
        self._cleanup()

        if uuid in self.cost_hash_map:
            entry = self.cost_hash_map[uuid]
            entry.cost += cost
            entry.last_accessed_at = time.time()
        else:
            self.cost_hash_map[uuid] = CostHashMapEntry(cost=cost, last_accessed_at=time.time())