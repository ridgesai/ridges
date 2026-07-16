from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api import incentives
from api.endpoints import scoring as scoring_endpoint
from utils.bittensor import HotkeySubnetInfo
from utils.incentives import RewardCandidate


def _candidate(hotkey: str, reward_score: float) -> RewardCandidate:
    return RewardCandidate(
        agent_id=uuid4(),
        miner_hotkey=hotkey,
        initial_reward_score=reward_score,
        approved_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def incentive_config(monkeypatch):
    incentives.get_subnet_hotkey_info.cache_clear()
    monkeypatch.setattr(incentives.config, "BURN", False)
    monkeypatch.setattr(incentives.config, "INCENTIVE_START_SET_ID", 10)
    monkeypatch.setattr(incentives.config, "INCENTIVE_REWARD_HALF_LIFE_HOURS", 336)
    yield
    incentives.get_subnet_hotkey_info.cache_clear()


@pytest.mark.anyio
async def test_active_weights_include_every_registered_candidate(monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    candidates = [
        _candidate("unregistered", 0.7),
        _candidate("hk-1", 0.6),
        _candidate("hk-2", 0.5),
        _candidate("hk-3", 0.4),
        _candidate("hk-4", 0.3),
    ]

    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return candidates, observed_at

    async def get_subnet_info():
        return {
            hotkey: HotkeySubnetInfo(uid=index, emission=0.0)
            for index, hotkey in enumerate(("hk-1", "hk-2", "hk-3", "hk-4"))
        }

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    allocations = await incentives.get_current_allocations()

    assert list(allocations.hotkey_weights) == ["hk-1", "hk-2", "hk-3", "hk-4"]
    assert sum(allocations.hotkey_weights.values()) == pytest.approx(1)
    assert list(allocations.hotkey_weights.values()) == sorted(allocations.hotkey_weights.values(), reverse=True)
    assert set(allocations.agent_weights) == {candidate.agent_id for candidate in candidates[1:]}


@pytest.mark.anyio
async def test_active_weights_sum_same_hotkey_agent_contributions(monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    first_shared = _candidate("shared-hk", 1)
    second_shared = _candidate("shared-hk", 2)
    other = _candidate("other-hk", 3)
    unregistered = _candidate("unregistered", 100)

    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return [unregistered, first_shared, second_shared, other], observed_at

    async def get_subnet_info():
        return {
            "shared-hk": HotkeySubnetInfo(uid=0, emission=0.0),
            "other-hk": HotkeySubnetInfo(uid=1, emission=0.0),
        }

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    allocations = await incentives.get_current_allocations()

    assert allocations.agent_weights == pytest.approx(
        {
            first_shared.agent_id: 1 / 6,
            second_shared.agent_id: 2 / 6,
            other.agent_id: 3 / 6,
        }
    )
    assert allocations.hotkey_weights == pytest.approx({"shared-hk": 3 / 6, "other-hk": 3 / 6})
    assert unregistered.agent_id not in allocations.agent_weights


@pytest.mark.anyio
async def test_active_weights_burn_only_when_no_candidate_is_registered(monkeypatch) -> None:
    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return [_candidate("missing", 0.6)], datetime.now(timezone.utc)

    async def get_subnet_info():
        return {}

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    allocations = await incentives.get_current_allocations()

    assert allocations == incentives.CurrentAllocations(
        hotkey_weights={incentives.config.OWNER_HOTKEY: 1.0},
        agent_weights={},
    )


@pytest.mark.anyio
async def test_subnet_snapshot_is_cached(monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    candidates = [_candidate("hk-1", 0.6), _candidate("hk-2", 0.5)]
    snapshot_calls = 0

    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return candidates, observed_at

    async def get_subnet_info():
        nonlocal snapshot_calls
        snapshot_calls += 1
        return {
            "hk-1": HotkeySubnetInfo(uid=0, emission=1.0),
            "hk-2": HotkeySubnetInfo(uid=1, emission=2.0),
        }

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    first = await incentives.get_current_allocations()
    second = await incentives.get_current_allocations()

    assert second == first
    assert snapshot_calls == 1


@pytest.mark.anyio
async def test_registration_rpc_failure_is_not_converted_to_burn(monkeypatch) -> None:
    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return [_candidate("hk", 0.6)], datetime.now(timezone.utc)

    async def fail_registration():
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", fail_registration)

    with pytest.raises(RuntimeError, match="rpc unavailable"):
        await incentives.get_current_allocations()


@pytest.mark.anyio
async def test_pre_activation_set_uses_legacy_receiver(monkeypatch) -> None:
    agent_id = uuid4()

    async def latest_set_id():
        return 9

    async def legacy_receiver():
        return {"agent_id": agent_id, "miner_hotkey": "legacy-hk"}

    async def get_subnet_info():
        return {"legacy-hk": HotkeySubnetInfo(uid=1, emission=1.0)}

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_weight_receiving_agent_info", legacy_receiver)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    assert await incentives.get_current_allocations() == incentives.CurrentAllocations(
        hotkey_weights={"legacy-hk": 1.0},
        agent_weights={agent_id: 1.0},
    )


@pytest.mark.anyio
async def test_scoring_weights_returns_only_aggregated_hotkey_weights(monkeypatch) -> None:
    agent_id = uuid4()
    allocations = incentives.CurrentAllocations(
        hotkey_weights={"shared-hk": 0.75, "other-hk": 0.25},
        agent_weights={agent_id: 0.75},
    )

    async def current_allocations():
        return allocations

    monkeypatch.setattr(scoring_endpoint, "get_current_allocations", current_allocations)

    assert await scoring_endpoint.weights() == allocations.hotkey_weights
