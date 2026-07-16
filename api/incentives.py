from dataclasses import dataclass
from uuid import UUID

import api.config as config
from queries.evaluation_set import get_latest_set_id
from queries.scores import get_incentive_reward_candidates, get_weight_receiving_agent_info
from utils.bittensor import HotkeySubnetInfo, subtensor_client
from utils.incentives import normalize_agent_reward_weights, rank_reward_candidates
from utils.ttl import ttl_cache


@ttl_cache(ttl_seconds=60, max_entries=1)
async def get_subnet_hotkey_info() -> dict[str, HotkeySubnetInfo]:
    return await subtensor_client.get_subnet_hotkey_info()


@dataclass(frozen=True, slots=True)
class CurrentAllocations:
    hotkey_weights: dict[str, float]
    agent_weights: dict[UUID, float]


async def get_current_allocations() -> CurrentAllocations:
    if config.BURN:
        return _owner_allocations()

    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return _owner_allocations()

    if latest_set_id < config.INCENTIVE_START_SET_ID:
        legacy_receiver = await get_weight_receiving_agent_info()
        if legacy_receiver is not None:
            hotkey = legacy_receiver["miner_hotkey"]
            subnet_hotkeys = await get_subnet_hotkey_info()
            if hotkey not in subnet_hotkeys:
                return _owner_allocations()
            return CurrentAllocations(
                hotkey_weights={hotkey: 1.0},
                agent_weights={legacy_receiver["agent_id"]: 1.0},
            )
        return _owner_allocations()

    candidates, observed_at = await get_incentive_reward_candidates(latest_set_id)
    ranked = rank_reward_candidates(
        candidates,
        observed_at=observed_at,
        reward_half_life_hours=config.INCENTIVE_REWARD_HALF_LIFE_HOURS,
    )
    if not ranked:
        return _owner_allocations()

    subnet_hotkeys = await get_subnet_hotkey_info()
    selected = [candidate for candidate in ranked if candidate.candidate.miner_hotkey in subnet_hotkeys]

    agent_weights = normalize_agent_reward_weights(selected)
    if not agent_weights:
        return _owner_allocations()

    hotkey_weights: dict[str, float] = {}
    for candidate in selected:
        hotkey = candidate.candidate.miner_hotkey
        weight = agent_weights[candidate.candidate.agent_id]
        hotkey_weights[hotkey] = hotkey_weights.get(hotkey, 0.0) + weight

    return CurrentAllocations(hotkey_weights=hotkey_weights, agent_weights=agent_weights)


def _owner_allocations() -> CurrentAllocations:
    return CurrentAllocations(hotkey_weights={config.OWNER_HOTKEY: 1.0}, agent_weights={})
