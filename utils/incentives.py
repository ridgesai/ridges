import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RelativeImprovement:
    """
    Result of comparing a candidate with the current eligible leader.
    Raw improvement measures the strongest performance or cost gain in threshold units.
    """

    qualified: bool
    performance_delta: float | None
    cost_delta: float | None
    raw_improvement: float


@dataclass(frozen=True, slots=True)
class RewardCandidate:
    """
    An approved agent eligible for reward ranking.
    Contains its final score and stored reward boost before time decay is applied.
    """

    agent_id: UUID
    miner_hotkey: str
    final_score: float
    initial_improvement_bonus: float
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class RankedRewardCandidate:
    """
    A reward candidate after applying bonus decay.
    Its reward score determines its position and proportional share of emissions.
    """

    candidate: RewardCandidate
    current_improvement_bonus: float
    reward_score: float


def _is_finite_nonnegative(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value >= 0


def _meets_threshold(value: float, threshold: float) -> bool:
    return value > threshold or math.isclose(value, threshold, rel_tol=1e-9, abs_tol=1e-12)


def calculate_relative_improvement(
    *,
    candidate_score: float,
    candidate_cost: float | None,
    leader_score: float | None,
    leader_cost: float | None,
    performance_threshold: float,
    cost_threshold: float,
) -> RelativeImprovement:
    """Compare a candidate with the current leader."""

    if performance_threshold <= 0 or cost_threshold <= 0:
        raise ValueError("Improvement thresholds must be positive")

    if not _is_finite_nonnegative(candidate_score):
        return RelativeImprovement(qualified=False, performance_delta=None, cost_delta=None, raw_improvement=0.0)

    # The first approved agent establishes the competition baseline.
    if leader_score is None:
        return RelativeImprovement(qualified=True, performance_delta=None, cost_delta=None, raw_improvement=0.0)
    if not _is_finite_nonnegative(leader_score):
        return RelativeImprovement(qualified=False, performance_delta=None, cost_delta=None, raw_improvement=0.0)

    # Calculate performance and cost deltas.
    if leader_score == 0:
        performance_delta = performance_threshold if candidate_score > 0 else 0.0
    else:
        performance_delta = (candidate_score - leader_score) / leader_score

    cost_delta = None
    if _is_finite_nonnegative(candidate_cost) and _is_finite_nonnegative(leader_cost) and leader_cost > 0:
        cost_delta = (leader_cost - candidate_cost) / leader_cost

    performance_ratio = performance_delta / performance_threshold
    cost_ratio = cost_delta / cost_threshold if cost_delta is not None else 0.0

    # Check if the candidate meets the performance and cost thresholds.
    performance_qualified = _meets_threshold(performance_delta, performance_threshold)
    cost_qualified = (
        cost_delta is not None and candidate_score >= leader_score and _meets_threshold(cost_delta, cost_threshold)
    )

    return RelativeImprovement(
        qualified=performance_qualified or cost_qualified,
        performance_delta=performance_delta,
        cost_delta=cost_delta,
        raw_improvement=max(0.0, performance_ratio, cost_ratio),
    )


def calculate_time_multiplier(*, elapsed_hours: float, half_life_hours: float, maximum: float) -> float:
    """
    Time value of Ridges: Increase the reward when a competition goes longer without improvement.
    The reward grows over time from 1x up to the configured maximum.
    """
    if half_life_hours <= 0:
        raise ValueError("Time multiplier half-life must be positive")
    if maximum < 1 or not math.isfinite(maximum):
        raise ValueError("Maximum time multiplier must be finite and at least 1")

    elapsed_hours = max(0.0, elapsed_hours)
    return 1 + (maximum - 1) * (1 - 2 ** (-elapsed_hours / half_life_hours))


def calculate_initial_improvement_bonus(
    *,
    raw_improvement: float,
    time_multiplier: float,
    bonus_at_threshold: float,
) -> float:
    """
    Set the agent's initial reward boost when it improves on the current leader.
    Bigger relative improvements earn more, amplified when the competition has gone longer without improvement.
    """
    if not _is_finite_nonnegative(raw_improvement):
        raise ValueError("Raw improvement must be finite and non-negative")
    if not math.isfinite(time_multiplier) or time_multiplier < 1:
        raise ValueError("Time multiplier must be finite and at least 1")
    if not _is_finite_nonnegative(bonus_at_threshold):
        raise ValueError("Improvement bonus at threshold must be finite and non-negative")

    return bonus_at_threshold * raw_improvement * time_multiplier


def decay_improvement_bonus(*, value: float, elapsed_hours: float, half_life_hours: float) -> float:
    """
    Fade an approved agent's reward boost over time.
    The remaining boost halves every configured half-life and approaches zero gradually.
    """
    if not _is_finite_nonnegative(value):
        raise ValueError("Improvement bonus must be finite and non-negative")
    if half_life_hours <= 0:
        raise ValueError("Improvement bonus half-life must be positive")

    return value * 2 ** (-max(0.0, elapsed_hours) / half_life_hours)


def rank_reward_candidates(
    candidates: list[RewardCandidate],
    *,
    observed_at: datetime,
    bonus_half_life_hours: float,
    bonus_cap: float,
) -> list[RankedRewardCandidate]:
    """
    Rank eligible agents by final score plus their remaining improvement boost.
    The boost decays from approval time and is capped; only the strongest agent per hotkey remains.
    Top-K selection and chain registration are handled by the caller.
    """
    if bonus_cap < 0 or not math.isfinite(bonus_cap):
        raise ValueError("Improvement bonus cap must be finite and non-negative")

    ranked: list[RankedRewardCandidate] = []
    for candidate in candidates:
        if not _is_finite_nonnegative(candidate.final_score) or candidate.final_score == 0:
            continue
        if not _is_finite_nonnegative(candidate.initial_improvement_bonus):
            continue

        elapsed_hours = max(0.0, (observed_at - candidate.approved_at).total_seconds() / 3600)
        current_improvement_bonus = decay_improvement_bonus(
            value=candidate.initial_improvement_bonus,
            elapsed_hours=elapsed_hours,
            half_life_hours=bonus_half_life_hours,
        )
        reward_score = candidate.final_score * (1 + min(current_improvement_bonus, bonus_cap))
        if math.isfinite(reward_score) and reward_score > 0:
            ranked.append(RankedRewardCandidate(candidate, current_improvement_bonus, reward_score))

    ranked.sort(
        key=lambda item: (
            -item.reward_score,
            -item.candidate.final_score,
            item.candidate.approved_at,
            str(item.candidate.agent_id),
        )
    )

    deduplicated: list[RankedRewardCandidate] = []
    seen_hotkeys: set[str] = set()
    for item in ranked:
        if item.candidate.miner_hotkey in seen_hotkeys:
            continue
        seen_hotkeys.add(item.candidate.miner_hotkey)
        deduplicated.append(item)

    return deduplicated


def normalize_reward_weights(candidates: list[RankedRewardCandidate]) -> dict[str, float]:
    total = sum(candidate.reward_score for candidate in candidates)
    if not math.isfinite(total) or total <= 0:
        return {}
    return {candidate.candidate.miner_hotkey: candidate.reward_score / total for candidate in candidates}
