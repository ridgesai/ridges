import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RelativeImprovement:
    """
    Result of comparing a candidate with the current eligible leader.
    Relative improvement combines positive compounded performance and cost gains in threshold units.
    """

    qualified: bool
    performance_delta: float | None
    cost_delta: float | None
    relative_improvement_units: float


@dataclass(frozen=True, slots=True)
class RewardCandidate:
    """
    An approved agent eligible for reward ranking.
    Contains its stored reward score before time decay is applied.
    """

    agent_id: UUID
    miner_hotkey: str
    initial_reward_score: float
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class RankedRewardCandidate:
    """
    A reward candidate after applying reward-score decay.
    Its current reward score determines its proportional share of emissions.
    """

    candidate: RewardCandidate
    current_reward_score: float


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

    if performance_threshold <= 0:
        raise ValueError("Performance improvement threshold must be positive")
    if not 0 < cost_threshold < 1:
        raise ValueError("Cost improvement threshold must be between 0 and 1")

    if not _is_finite_nonnegative(candidate_score):
        return RelativeImprovement(
            qualified=False,
            performance_delta=None,
            cost_delta=None,
            relative_improvement_units=0.0,
        )

    # The first approved agent establishes the competition baseline.
    if leader_score is None:
        return RelativeImprovement(
            qualified=True,
            performance_delta=None,
            cost_delta=None,
            relative_improvement_units=1.0,
        )
    if not _is_finite_nonnegative(leader_score):
        return RelativeImprovement(
            qualified=False,
            performance_delta=None,
            cost_delta=None,
            relative_improvement_units=0.0,
        )

    # Calculate performance and cost deltas.
    if leader_score == 0:
        performance_delta = performance_threshold if candidate_score > 0 else 0.0
    else:
        performance_delta = (candidate_score - leader_score) / leader_score

    cost_delta = None
    if _is_finite_nonnegative(candidate_cost) and _is_finite_nonnegative(leader_cost) and leader_cost > 0:
        cost_delta = (leader_cost - candidate_cost) / leader_cost

    performance_units = (
        math.log1p(performance_delta) / math.log1p(performance_threshold) if performance_delta > 0 else 0.0
    )
    cost_units = 0.0
    if cost_delta is not None and cost_delta > 0:
        maximum_cost_units = 1 / cost_threshold
        cost_units = (
            maximum_cost_units
            if cost_delta >= 1
            else min(math.log1p(-cost_delta) / math.log1p(-cost_threshold), maximum_cost_units)
        )

    # Check if the candidate meets the performance and cost thresholds.
    performance_qualified = _meets_threshold(performance_delta, performance_threshold)
    cost_qualified = (
        cost_delta is not None and candidate_score >= leader_score and _meets_threshold(cost_delta, cost_threshold)
    )

    return RelativeImprovement(
        qualified=performance_qualified or cost_qualified,
        performance_delta=performance_delta,
        cost_delta=cost_delta,
        relative_improvement_units=performance_units + cost_units,
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


def calculate_initial_reward_score(
    *,
    relative_improvement_units: float,
    time_multiplier: float,
) -> float:
    """
    Set the agent's initial reward score when it improves on the current leader.
    Bigger relative improvements earn more, amplified when the competition has gone longer without improvement.
    """
    if not _is_finite_nonnegative(relative_improvement_units):
        raise ValueError("Relative improvement units must be finite and non-negative")
    if not math.isfinite(time_multiplier) or time_multiplier < 1:
        raise ValueError("Time multiplier must be finite and at least 1")

    return relative_improvement_units * time_multiplier


def decay_reward_score(*, value: float, elapsed_hours: float, half_life_hours: float) -> float:
    """
    Fade an approved agent's reward score over time.
    The remaining score halves every configured half-life and approaches zero gradually.
    """
    if not _is_finite_nonnegative(value):
        raise ValueError("Reward score must be finite and non-negative")
    if half_life_hours <= 0:
        raise ValueError("Reward half-life must be positive")

    return value * 2 ** (-max(0.0, elapsed_hours) / half_life_hours)


def rank_reward_candidates(
    candidates: list[RewardCandidate],
    *,
    observed_at: datetime,
    reward_half_life_hours: float,
) -> list[RankedRewardCandidate]:
    """
    Rank eligible agents by their remaining reward score.
    The score decays from approval time. Hotkey aggregation is handled by the caller.
    Chain registration is handled by the caller.
    """
    ranked: list[RankedRewardCandidate] = []
    for candidate in candidates:
        if not _is_finite_nonnegative(candidate.initial_reward_score) or candidate.initial_reward_score == 0:
            continue

        elapsed_hours = max(0.0, (observed_at - candidate.approved_at).total_seconds() / 3600)
        current_reward_score = decay_reward_score(
            value=candidate.initial_reward_score,
            elapsed_hours=elapsed_hours,
            half_life_hours=reward_half_life_hours,
        )
        if math.isfinite(current_reward_score) and current_reward_score > 0:
            ranked.append(RankedRewardCandidate(candidate, current_reward_score))

    ranked.sort(
        key=lambda item: (
            -item.current_reward_score,
            item.candidate.approved_at,
            str(item.candidate.agent_id),
        )
    )

    return ranked


def normalize_agent_reward_weights(candidates: list[RankedRewardCandidate]) -> dict[UUID, float]:
    total = sum(candidate.current_reward_score for candidate in candidates)
    if not math.isfinite(total) or total <= 0:
        return {}
    return {candidate.candidate.agent_id: candidate.current_reward_score / total for candidate in candidates}
