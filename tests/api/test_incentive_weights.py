from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api import incentives
from utils.incentives import RewardCandidate


def _candidate(hotkey: str, score: float, bonus: float = 0) -> RewardCandidate:
    return RewardCandidate(
        agent_id=uuid4(),
        miner_hotkey=hotkey,
        final_score=score,
        initial_improvement_bonus=bonus,
        approved_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def incentive_config(monkeypatch):
    incentives._get_reward_hotkey_uids.cache_clear()
    monkeypatch.setattr(incentives.config, "BURN", False)
    monkeypatch.setattr(incentives.config, "INCENTIVE_START_SET_ID", 10)
    monkeypatch.setattr(incentives.config, "INCENTIVE_TOP_K", 3)
    yield
    incentives._get_reward_hotkey_uids.cache_clear()


@pytest.mark.anyio
async def test_active_weights_skip_unregistered_and_fill_all_three_slots(monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    candidates = [
        _candidate("unregistered", 0.7),
        _candidate("hk-1", 0.6),
        _candidate("hk-2", 0.5),
        _candidate("hk-3", 0.4),
    ]

    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return candidates, observed_at

    async def get_uids(hotkeys):
        return {hotkey: None if hotkey == "unregistered" else 1 for hotkey in hotkeys}

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_uids_for_hotkeys_on_subnet", get_uids)

    allocations = await incentives.get_current_allocations()

    assert [allocation.miner_hotkey for allocation in allocations] == ["hk-1", "hk-2", "hk-3"]
    assert sum(allocation.weight for allocation in allocations) == pytest.approx(1)
    assert allocations[0].weight > allocations[1].weight > allocations[2].weight


@pytest.mark.anyio
async def test_active_weights_burn_only_when_no_candidate_is_registered(monkeypatch) -> None:
    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return [_candidate("missing", 0.6)], datetime.now(timezone.utc)

    async def get_uids(hotkeys):
        return dict.fromkeys(hotkeys)

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_uids_for_hotkeys_on_subnet", get_uids)

    allocations = await incentives.get_current_allocations()

    assert allocations == [incentives.CurrentAllocation(None, incentives.config.OWNER_HOTKEY, 1.0)]


@pytest.mark.anyio
async def test_registration_checks_are_batched_and_cached(monkeypatch) -> None:
    observed_at = datetime.now(timezone.utc)
    candidates = [_candidate("hk-1", 0.6), _candidate("hk-2", 0.5)]
    registration_calls = []

    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return candidates, observed_at

    async def get_uids(hotkeys):
        registration_calls.append(hotkeys)
        return {hotkey: index for index, hotkey in enumerate(hotkeys)}

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_uids_for_hotkeys_on_subnet", get_uids)

    first = await incentives.get_current_allocations()
    second = await incentives.get_current_allocations()

    assert second == first
    assert registration_calls == [("hk-1", "hk-2")]


@pytest.mark.anyio
async def test_registration_rpc_failure_is_not_converted_to_burn(monkeypatch) -> None:
    async def latest_set_id():
        return 10

    async def reward_candidates(_set_id):
        return [_candidate("hk", 0.6)], datetime.now(timezone.utc)

    async def fail_registration(_hotkeys):
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_incentive_reward_candidates", reward_candidates)
    monkeypatch.setattr(incentives.subtensor_client, "get_uids_for_hotkeys_on_subnet", fail_registration)

    with pytest.raises(RuntimeError, match="rpc unavailable"):
        await incentives.get_current_allocations()


@pytest.mark.anyio
async def test_pre_activation_set_uses_legacy_receiver(monkeypatch) -> None:
    agent_id = uuid4()

    async def latest_set_id():
        return 9

    async def legacy_receiver():
        return {"agent_id": agent_id, "miner_hotkey": "legacy-hk"}

    async def get_uids(hotkeys):
        return {hotkey: 1 for hotkey in hotkeys}

    monkeypatch.setattr(incentives, "get_latest_set_id", latest_set_id)
    monkeypatch.setattr(incentives, "get_weight_receiving_agent_info", legacy_receiver)
    monkeypatch.setattr(incentives.subtensor_client, "get_uids_for_hotkeys_on_subnet", get_uids)

    assert await incentives.get_current_allocations() == [incentives.CurrentAllocation(agent_id, "legacy-hk", 1.0)]
