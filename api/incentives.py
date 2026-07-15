from dataclasses import dataclass
from uuid import UUID

import api.config as config
from queries.evaluation_set import get_latest_set_id
from queries.scores import get_incentive_reward_candidates, get_weight_receiving_agent_info
from utils.bittensor import subtensor_client
from utils.incentives import normalize_reward_weights, rank_reward_candidates
from utils.ttl import ttl_cache


@ttl_cache(ttl_seconds=60, max_entries=8)
async def _get_reward_hotkey_uids(hotkeys: tuple[str, ...]) -> dict[str, int | None]:
    return await subtensor_client.get_uids_for_hotkeys_on_subnet(hotkeys)


@dataclass(frozen=True, slots=True)
class CurrentAllocation:
    agent_id: UUID | None
    miner_hotkey: str
    weight: float


async def get_current_allocations() -> list[CurrentAllocation]:
    if config.BURN:
        return [_owner_allocation()]

    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return [_owner_allocation()]

    if latest_set_id < config.INCENTIVE_START_SET_ID:
        legacy_receiver = await get_weight_receiving_agent_info()
        if legacy_receiver is not None:
            hotkey = legacy_receiver["miner_hotkey"]
            uids = await _get_reward_hotkey_uids((hotkey,))
            if uids[hotkey] is None:
                return [_owner_allocation()]
            return [
                CurrentAllocation(
                    agent_id=legacy_receiver["agent_id"],
                    miner_hotkey=hotkey,
                    weight=1.0,
                )
            ]
        return [_owner_allocation()]

    candidates, observed_at = await get_incentive_reward_candidates(latest_set_id)
    ranked = rank_reward_candidates(
        candidates,
        observed_at=observed_at,
        bonus_half_life_hours=config.INCENTIVE_IMPROVEMENT_BONUS_HALF_LIFE_HOURS,
        bonus_cap=config.INCENTIVE_IMPROVEMENT_BONUS_CAP,
    )

    hotkeys = tuple(sorted(candidate.candidate.miner_hotkey for candidate in ranked))
    uids = await _get_reward_hotkey_uids(hotkeys)
    selected = [candidate for candidate in ranked if uids[candidate.candidate.miner_hotkey] is not None][
        : config.INCENTIVE_TOP_K
    ]

    weights = normalize_reward_weights(selected)
    if not weights:
        return [_owner_allocation()]
    return [
        CurrentAllocation(
            agent_id=candidate.candidate.agent_id,
            miner_hotkey=candidate.candidate.miner_hotkey,
            weight=weights[candidate.candidate.miner_hotkey],
        )
        for candidate in selected
    ]


def _owner_allocation() -> CurrentAllocation:
    return CurrentAllocation(agent_id=None, miner_hotkey=config.OWNER_HOTKEY, weight=1.0)
