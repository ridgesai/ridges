"""Tests for inference_gateway/cost_hash_map.py — in-memory cost tracking."""

import time
import pytest

from uuid import uuid4
from inference_gateway.cost_hash_map import CostHashMap, CostHashMapEntry, COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS


class TestCostHashMapEntry:
    """Tests for CostHashMapEntry model."""

    def test_creation(self):
        entry = CostHashMapEntry(cost=1.5, last_accessed_at=time.time())
        assert entry.cost == 1.5

    def test_zero_cost(self):
        entry = CostHashMapEntry(cost=0.0, last_accessed_at=time.time())
        assert entry.cost == 0.0


class TestCostHashMapGetCost:
    """Tests for CostHashMap.get_cost."""

    def test_unknown_uuid_returns_zero(self):
        chm = CostHashMap()
        assert chm.get_cost(uuid4()) == 0

    def test_known_uuid_returns_cost(self):
        chm = CostHashMap()
        uid = uuid4()
        chm.add_cost(uid, 3.14)
        assert chm.get_cost(uid) == 3.14

    def test_multiple_uuids_independent(self):
        chm = CostHashMap()
        uid1, uid2 = uuid4(), uuid4()
        chm.add_cost(uid1, 1.0)
        chm.add_cost(uid2, 2.0)
        assert chm.get_cost(uid1) == 1.0
        assert chm.get_cost(uid2) == 2.0


class TestCostHashMapAddCost:
    """Tests for CostHashMap.add_cost."""

    def test_add_cost_accumulates(self):
        chm = CostHashMap()
        uid = uuid4()
        chm.add_cost(uid, 1.0)
        chm.add_cost(uid, 2.5)
        chm.add_cost(uid, 0.5)
        assert chm.get_cost(uid) == 4.0

    def test_add_cost_creates_entry(self):
        chm = CostHashMap()
        uid = uuid4()
        chm.add_cost(uid, 5.0)
        assert uid in chm.cost_hash_map
        assert chm.cost_hash_map[uid].cost == 5.0

    def test_add_cost_updates_last_accessed(self):
        chm = CostHashMap()
        uid = uuid4()
        before = time.time()
        chm.add_cost(uid, 1.0)
        after = time.time()
        assert before <= chm.cost_hash_map[uid].last_accessed_at <= after


class TestCostHashMapCleanup:
    """Tests for CostHashMap._cleanup method."""

    def test_cleanup_removes_stale_entries(self):
        chm = CostHashMap()
        uid = uuid4()
        chm.add_cost(uid, 1.0)
        # Manually age the entry
        chm.cost_hash_map[uid].last_accessed_at = time.time() - COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 10
        # Force cleanup by setting last_cleanup_at to the past
        chm.last_cleanup_at = time.time() - COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 10
        chm._cleanup()
        assert uid not in chm.cost_hash_map

    def test_cleanup_preserves_fresh_entries(self):
        chm = CostHashMap()
        uid = uuid4()
        chm.add_cost(uid, 1.0)
        chm.last_cleanup_at = time.time() - COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 10
        chm._cleanup()
        assert uid in chm.cost_hash_map

    def test_cleanup_skipped_when_recent(self):
        chm = CostHashMap()
        stale_uid = uuid4()
        chm.add_cost(stale_uid, 1.0)
        chm.cost_hash_map[stale_uid].last_accessed_at = time.time() - COST_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 10
        # last_cleanup_at is recent, so cleanup should not run
        chm.last_cleanup_at = time.time()
        chm._cleanup()
        # Stale entry should still be there since cleanup was skipped
        assert stale_uid in chm.cost_hash_map
