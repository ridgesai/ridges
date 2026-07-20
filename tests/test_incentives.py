import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from utils.incentives import (
    RewardCandidate,
    calculate_initial_reward_score,
    calculate_relative_improvement,
    calculate_time_multiplier,
    decay_reward_score,
    normalize_agent_reward_weights,
    rank_reward_candidates,
)


def _improvement(*, candidate_score=0.6, candidate_cost=0.1, leader_score=0.5, leader_cost=0.1):
    return calculate_relative_improvement(
        candidate_score=candidate_score,
        candidate_cost=candidate_cost,
        leader_score=leader_score,
        leader_cost=leader_cost,
        performance_threshold=0.03,
        cost_threshold=0.06,
    )


def test_first_leader_qualifies_with_one_relative_improvement_unit() -> None:
    result = _improvement(leader_score=None, leader_cost=None)

    assert result.qualified is True
    assert result.relative_improvement_units == 1


@pytest.mark.parametrize("candidate_score", [0.515, 0.5150000000000001])
def test_exact_performance_threshold_qualifies(candidate_score: float) -> None:
    result = _improvement(candidate_score=candidate_score)

    assert result.qualified is True
    assert result.relative_improvement_units == pytest.approx(1)


def test_large_performance_jump_uses_compounded_units() -> None:
    result = _improvement(candidate_score=0.3, leader_score=0.1)

    expected_units = math.log(3) / math.log1p(0.03)
    assert result.qualified is True
    assert result.relative_improvement_units == pytest.approx(expected_units)
    assert result.relative_improvement_units == pytest.approx(37.17, rel=1e-4)


def test_compounded_performance_units_are_path_independent() -> None:
    direct = _improvement(candidate_score=0.3, leader_score=0.1)
    first_step = _improvement(candidate_score=0.15, leader_score=0.1)
    second_step = _improvement(candidate_score=0.3, leader_score=0.15)

    assert direct.relative_improvement_units == pytest.approx(
        first_step.relative_improvement_units + second_step.relative_improvement_units
    )


def test_exact_cost_threshold_requires_no_performance_regression() -> None:
    equal_score = _improvement(candidate_score=0.5, candidate_cost=0.094)
    lower_score = _improvement(candidate_score=0.499, candidate_cost=0.088)

    assert equal_score.qualified is True
    assert equal_score.cost_delta == pytest.approx(0.06)
    assert equal_score.relative_improvement_units == pytest.approx(1)
    assert lower_score.qualified is False


def test_compounded_cost_units_are_path_independent() -> None:
    direct = _improvement(candidate_score=0.5, candidate_cost=0.05, leader_cost=0.1)
    first_step = _improvement(candidate_score=0.5, candidate_cost=0.075, leader_cost=0.1)
    second_step = _improvement(candidate_score=0.5, candidate_cost=0.05, leader_cost=0.075)

    assert direct.relative_improvement_units == pytest.approx(
        first_step.relative_improvement_units + second_step.relative_improvement_units
    )


@pytest.mark.parametrize("candidate_cost", [0, 1e-12])
def test_zero_and_near_zero_cost_use_the_finite_maximum(candidate_cost: float) -> None:
    result = _improvement(candidate_score=0.5, candidate_cost=candidate_cost)

    assert result.qualified is True
    assert result.relative_improvement_units == pytest.approx(1 / 0.06)


def test_relative_improvement_sums_positive_performance_and_cost_units() -> None:
    result = _improvement(candidate_score=0.53, candidate_cost=0.09)

    assert result.performance_delta == pytest.approx(0.06)
    assert result.cost_delta == pytest.approx(0.10)
    expected_performance_units = math.log1p(0.06) / math.log1p(0.03)
    expected_cost_units = math.log1p(-0.10) / math.log1p(-0.06)
    assert result.relative_improvement_units == pytest.approx(expected_performance_units + expected_cost_units)


def test_cost_qualified_candidate_also_receives_fractional_performance_units() -> None:
    result = _improvement(candidate_score=0.505, candidate_cost=0.094)

    assert result.qualified is True
    assert result.performance_delta == pytest.approx(0.01)
    assert result.cost_delta == pytest.approx(0.06)
    expected_performance_units = math.log1p(0.01) / math.log1p(0.03)
    assert result.relative_improvement_units == pytest.approx(expected_performance_units + 1)


def test_worse_cost_does_not_reduce_performance_units() -> None:
    result = _improvement(candidate_score=0.53, candidate_cost=0.11)

    assert result.performance_delta == pytest.approx(0.06)
    assert result.cost_delta == pytest.approx(-0.10)
    assert result.relative_improvement_units == pytest.approx(math.log1p(0.06) / math.log1p(0.03))


def test_performance_can_qualify_with_higher_or_missing_cost() -> None:
    assert _improvement(candidate_score=0.52, candidate_cost=1).qualified is True
    assert _improvement(candidate_score=0.52, candidate_cost=None).qualified is True


def test_zero_score_leader_produces_one_finite_improvement_unit() -> None:
    result = _improvement(candidate_score=0.2, leader_score=0)

    assert result.qualified is True
    assert result.relative_improvement_units == pytest.approx(1)


def test_zero_score_candidate_does_not_improve_zero_score_leader() -> None:
    result = _improvement(candidate_score=0, leader_score=0)

    assert result.qualified is False
    assert result.relative_improvement_units == 0


@pytest.mark.parametrize("leader_cost", [None, 0])
def test_missing_or_zero_leader_cost_produces_no_cost_units(leader_cost: float | None) -> None:
    result = _improvement(candidate_score=0.515, candidate_cost=0, leader_cost=leader_cost)

    assert result.qualified is True
    assert result.cost_delta is None
    assert result.relative_improvement_units == pytest.approx(1)


@pytest.mark.parametrize("candidate_score", [-1, float("nan"), float("inf")])
def test_invalid_candidate_score_cannot_qualify(candidate_score: float) -> None:
    result = _improvement(candidate_score=candidate_score)

    assert result.qualified is False
    assert result.relative_improvement_units == 0


def test_time_multiplier_grows_smoothly_and_is_bounded() -> None:
    assert calculate_time_multiplier(elapsed_hours=0, half_life_hours=72, maximum=2) == 1
    assert calculate_time_multiplier(elapsed_hours=72, half_life_hours=72, maximum=2) == pytest.approx(1.5)
    assert calculate_time_multiplier(elapsed_hours=144, half_life_hours=72, maximum=2) == pytest.approx(1.75)
    assert calculate_time_multiplier(elapsed_hours=10_000, half_life_hours=72, maximum=2) <= 2


def test_initial_reward_score_uses_time_adjusted_relative_improvement() -> None:
    result = calculate_initial_reward_score(
        relative_improvement_units=2,
        time_multiplier=1.5,
    )

    assert result == pytest.approx(3)
    assert decay_reward_score(value=result, elapsed_hours=336, half_life_hours=336) == pytest.approx(1.5)


def test_ranking_uses_current_reward_score_and_retains_same_hotkey_agents() -> None:
    now = datetime.now(timezone.utc)
    candidates = [
        RewardCandidate(uuid4(), "hk-a", 1.0, now),
        RewardCandidate(uuid4(), "hk-a-new", 2.0, now),
        RewardCandidate(uuid4(), "hk-b", 1.5, now),
        RewardCandidate(uuid4(), "hk-b", 0.5, now),
    ]

    ranked = rank_reward_candidates(
        candidates,
        observed_at=now,
        reward_half_life_hours=336,
    )

    assert [item.candidate.miner_hotkey for item in ranked] == ["hk-a-new", "hk-b", "hk-a", "hk-b"]
    weights = normalize_agent_reward_weights(ranked)
    assert sum(weights.values()) == pytest.approx(1)
    assert [weights[item.candidate.agent_id] for item in ranked] == sorted(weights.values(), reverse=True)


def test_reward_score_decays_without_mutating_initial_score() -> None:
    approved_at = datetime.now(timezone.utc)
    candidate = RewardCandidate(uuid4(), "hk", 0.5, approved_at)

    ranked = rank_reward_candidates(
        [candidate],
        observed_at=approved_at + timedelta(hours=336),
        reward_half_life_hours=336,
    )

    assert ranked[0].current_reward_score == pytest.approx(0.25)
    assert candidate.initial_reward_score == 0.5


def test_relative_reward_scores_are_linear_and_uncapped() -> None:
    now = datetime.now(timezone.utc)
    one_unit_agent_id = uuid4()
    ten_unit_agent_id = uuid4()
    ranked = rank_reward_candidates(
        [
            RewardCandidate(one_unit_agent_id, "one-unit", 1, now),
            RewardCandidate(ten_unit_agent_id, "ten-units", 10, now),
        ],
        observed_at=now,
        reward_half_life_hours=336,
    )

    weights = normalize_agent_reward_weights(ranked)
    assert weights == pytest.approx({ten_unit_agent_id: 10 / 11, one_unit_agent_id: 1 / 11})


def test_normalized_ratios_stay_constant_when_candidate_pool_does_not_change() -> None:
    approved_at = datetime.now(timezone.utc)
    candidates = [
        RewardCandidate(UUID(int=1), "hk-1", 1, approved_at),
        RewardCandidate(UUID(int=2), "hk-2", 2, approved_at),
    ]

    initial = normalize_agent_reward_weights(
        rank_reward_candidates(candidates, observed_at=approved_at, reward_half_life_hours=336)
    )
    later = normalize_agent_reward_weights(
        rank_reward_candidates(
            candidates,
            observed_at=approved_at + timedelta(hours=336),
            reward_half_life_hours=336,
        )
    )

    assert later == pytest.approx(initial)
