import math
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from bittensor.utils.weight_utils import convert_and_normalize_weights_and_uids

import api.config as config
from models.competition import exact_decimal_sum
from queries.scores import CompetitionWeightInput, WeightCalculationSnapshot, get_weight_calculation_snapshot
from utils.bittensor import HotkeySubnetInfo, subtensor_client
from utils.incentives import normalize_agent_reward_weights, rank_reward_candidates
from utils.ttl import ttl_cache


@ttl_cache(ttl_seconds=60)
async def get_subnet_hotkey_info() -> dict[str, HotkeySubnetInfo]:
    """Cached metagraph data for public presentation, not weight eligibility."""
    return await subtensor_client.get_subnet_hotkey_info()


@dataclass(frozen=True, slots=True)
class CurrentAllocations:
    hotkey_weights: dict[str, float]
    agent_weights: dict[UUID, float]


def _is_emission_active(competition: CompetitionWeightInput, observed_at) -> bool:
    return (
        competition.start_date is not None
        and not competition.is_paused
        and competition.end_date is None
        and (competition.emissions_end_at is None or observed_at < competition.emissions_end_at)
    )


def _positive_float(value: Decimal, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must remain finite and positive in weight arithmetic")
    return result


def _validate_source_representability(
    *,
    set_id: int,
    contributions: dict[str, float],
    final_maximum: float,
) -> None:
    source_uids = list(range(1, len(contributions) + 1))
    encoded_uids, _encoded_weights = convert_and_normalize_weights_and_uids(
        [0, *source_uids],
        [final_maximum, *contributions.values()],
    )
    if not any(uid in source_uids for uid in encoded_uids):
        raise ValueError(f"Competition {set_id} has no contribution under the pinned chain encoding")


def calculate_current_allocations(
    snapshot: WeightCalculationSnapshot,
    subnet_hotkeys: dict[str, HotkeySubnetInfo],
) -> CurrentAllocations:
    raw_weights: list[Decimal] = []
    for competition in snapshot.competitions:
        raw_weight = competition.raw_emission_weight
        if not raw_weight.is_finite() or raw_weight < 0 or raw_weight > 1:
            raise ValueError(f"Competition {competition.set_id} has an invalid raw emission weight")
        raw_weights.append(raw_weight)

    raw_total = exact_decimal_sum(raw_weights)
    if not raw_total.is_finite() or raw_total > 1:
        raise ValueError("Raw competition emission weights must have a finite total no greater than one")

    owner_weight_parts = [exact_decimal_sum([Decimal("1"), raw_total.copy_negate()])]
    hotkey_weights: dict[str, float] = {}
    agent_weights: dict[UUID, float] = {}
    source_contributions: list[tuple[int, dict[str, float]]] = []

    for competition in snapshot.competitions:
        raw_weight = competition.raw_emission_weight
        if raw_weight == 0:
            continue

        if not _is_emission_active(competition, snapshot.observed_at):
            owner_weight_parts.append(raw_weight)
            continue

        if competition.policy is None:
            raise ValueError(f"Active competition {competition.set_id} has no complete policy")

        competition_hotkey_weights: dict[str, float] = {}

        if competition.policy.incentive_enabled:
            ranked = rank_reward_candidates(
                list(competition.incentive_candidates),
                observed_at=snapshot.observed_at,
                reward_half_life_hours=competition.policy.incentive_reward_half_life_hours,
            )
            registered = [candidate for candidate in ranked if candidate.candidate.miner_hotkey in subnet_hotkeys]
            normalized = normalize_agent_reward_weights(registered)
            if normalized:
                raw_weight_float = _positive_float(
                    raw_weight,
                    label=f"Competition {competition.set_id} raw emission weight",
                )
                for candidate in registered:
                    agent_id = candidate.candidate.agent_id
                    contribution = normalized[agent_id] * raw_weight_float
                    if not math.isfinite(contribution) or contribution < 0:
                        raise ValueError(f"Competition {competition.set_id} produced an invalid contribution")

                    if contribution == 0:
                        continue

                    hotkey = candidate.candidate.miner_hotkey
                    competition_hotkey_weights[hotkey] = competition_hotkey_weights.get(hotkey, 0.0) + contribution
                    agent_weights[agent_id] = agent_weights.get(agent_id, 0.0) + contribution

                if not competition_hotkey_weights:
                    raise ValueError(
                        f"Competition {competition.set_id} has a positive distribution that underflows in weight arithmetic"
                    )
        else:
            receiver = competition.legacy_receiver
            if receiver is not None and receiver.miner_hotkey in subnet_hotkeys:
                raw_weight_float = _positive_float(
                    raw_weight,
                    label=f"Competition {competition.set_id} raw emission weight",
                )
                competition_hotkey_weights[receiver.miner_hotkey] = raw_weight_float
                agent_weights[receiver.agent_id] = agent_weights.get(receiver.agent_id, 0.0) + raw_weight_float

        if not competition_hotkey_weights:
            owner_weight_parts.append(raw_weight)
            continue

        for hotkey, contribution in competition_hotkey_weights.items():
            hotkey_weights[hotkey] = hotkey_weights.get(hotkey, 0.0) + contribution
        source_contributions.append((competition.set_id, competition_hotkey_weights))

    owner_weight = exact_decimal_sum(owner_weight_parts)
    if not owner_weight.is_finite() or owner_weight < 0:
        raise ValueError("Owner emission weight must be finite and non-negative")

    if owner_weight > 0:
        if config.OWNER_HOTKEY not in subnet_hotkeys:
            raise ValueError("Configured owner hotkey is not registered on the subnet")

        owner_weight_float = float(owner_weight)
        if not math.isfinite(owner_weight_float) or owner_weight_float < 0:
            raise ValueError("Owner emission weight must remain finite and non-negative in weight arithmetic")

        if owner_weight_float > 0:
            hotkey_weights[config.OWNER_HOTKEY] = hotkey_weights.get(config.OWNER_HOTKEY, 0.0) + owner_weight_float

    if not hotkey_weights or any(not math.isfinite(weight) or weight <= 0 for weight in hotkey_weights.values()):
        raise ValueError("Calculated hotkey weights must be nonempty, finite, and positive")

    total = math.fsum(hotkey_weights.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"Calculated hotkey weights must total one, got {total}")

    final_maximum = max(hotkey_weights.values())
    final_uids, _final_weights = convert_and_normalize_weights_and_uids(
        list(range(len(hotkey_weights))),
        list(hotkey_weights.values()),
    )
    if not final_uids:
        raise ValueError("Calculated hotkey weights are empty under the pinned chain encoding")

    for set_id, contributions in source_contributions:
        _validate_source_representability(
            set_id=set_id,
            contributions=contributions,
            final_maximum=final_maximum,
        )

    return CurrentAllocations(hotkey_weights=hotkey_weights, agent_weights=agent_weights)


async def get_current_allocations() -> CurrentAllocations:
    snapshot = await get_weight_calculation_snapshot()
    subnet_hotkeys = await subtensor_client.get_subnet_hotkey_info()
    return calculate_current_allocations(snapshot, subnet_hotkeys)
