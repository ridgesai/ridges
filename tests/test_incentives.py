from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from utils.incentives import (
    RewardCandidate,
    calculate_initial_improvement_bonus,
    calculate_relative_improvement,
    calculate_time_multiplier,
    decay_improvement_bonus,
    normalize_reward_weights,
    rank_reward_candidates,
)


def _improvement(*, candidate_score=0.6, candidate_cost=0.1, leader_score=0.5, leader_cost=0.1):
    return calculate_relative_improvement(
        candidate_score=candidate_score,
        candidate_cost=candidate_cost,
        leader_score=leader_score,
        leader_cost=leader_cost,
        performance_threshold=0.03,
        cost_threshold=0.05,
    )


def test_first_leader_qualifies_without_an_improvement_bonus() -> None:
    result = _improvement(leader_score=None, leader_cost=None)

    assert result.qualified is True
    assert result.raw_improvement == 0


@pytest.mark.parametrize("candidate_score", [0.515, 0.5150000000000001])
def test_exact_performance_threshold_qualifies(candidate_score: float) -> None:
    result = _improvement(candidate_score=candidate_score)

    assert result.qualified is True
    assert result.raw_improvement == pytest.approx(1)


def test_cost_threshold_requires_no_performance_regression() -> None:
    equal_score = _improvement(candidate_score=0.5, candidate_cost=0.095)
    lower_score = _improvement(candidate_score=0.499, candidate_cost=0.09)

    assert equal_score.qualified is True
    assert equal_score.cost_delta == pytest.approx(0.05)
    assert lower_score.qualified is False


def test_performance_can_qualify_with_higher_or_missing_cost() -> None:
    assert _improvement(candidate_score=0.52, candidate_cost=1).qualified is True
    assert _improvement(candidate_score=0.52, candidate_cost=None).qualified is True


def test_zero_score_leader_produces_finite_threshold_improvement() -> None:
    result = _improvement(candidate_score=0.2, leader_score=0)

    assert result.qualified is True
    assert result.raw_improvement == pytest.approx(1)


@pytest.mark.parametrize("candidate_score", [-1, float("nan"), float("inf")])
def test_invalid_candidate_score_cannot_qualify(candidate_score: float) -> None:
    assert _improvement(candidate_score=candidate_score).qualified is False


def test_time_multiplier_grows_smoothly_and_is_bounded() -> None:
    assert calculate_time_multiplier(elapsed_hours=0, half_life_hours=72, maximum=2) == 1
    assert calculate_time_multiplier(elapsed_hours=72, half_life_hours=72, maximum=2) == pytest.approx(1.5)
    assert calculate_time_multiplier(elapsed_hours=144, half_life_hours=72, maximum=2) == pytest.approx(1.75)
    assert calculate_time_multiplier(elapsed_hours=10_000, half_life_hours=72, maximum=2) <= 2


def test_initial_bonus_uses_time_adjusted_relative_improvement() -> None:
    result = calculate_initial_improvement_bonus(
        raw_improvement=1,
        time_multiplier=1.5,
        bonus_at_threshold=0.25,
    )

    assert result == pytest.approx(0.375)
    assert decay_improvement_bonus(value=result, elapsed_hours=72, half_life_hours=72) == pytest.approx(0.1875)


def test_ranking_uses_reward_score_and_deduplicates_only_hotkeys() -> None:
    now = datetime.now(timezone.utc)
    candidates = [
        RewardCandidate(uuid4(), "hk-a", 0.6, 0, now),
        RewardCandidate(uuid4(), "hk-a-new", 0.5, 0.5, now),
        RewardCandidate(uuid4(), "hk-b", 0.5, 0, now),
        RewardCandidate(uuid4(), "hk-b", 0.49, 0.5, now),
    ]

    ranked = rank_reward_candidates(
        candidates,
        observed_at=now,
        bonus_half_life_hours=72,
        bonus_cap=0.5,
    )

    assert [item.candidate.miner_hotkey for item in ranked] == ["hk-a-new", "hk-b", "hk-a"]
    weights = normalize_reward_weights(ranked)
    assert sum(weights.values()) == pytest.approx(1)
    assert weights["hk-a-new"] > weights["hk-b"]


def test_reward_bonus_decays_without_mutating_initial_bonus() -> None:
    approved_at = datetime.now(timezone.utc)
    candidate = RewardCandidate(uuid4(), "hk", 0.5, 0.5, approved_at)

    ranked = rank_reward_candidates(
        [candidate],
        observed_at=approved_at + timedelta(hours=72),
        bonus_half_life_hours=72,
        bonus_cap=0.5,
    )

    assert ranked[0].current_improvement_bonus == pytest.approx(0.25)
    assert ranked[0].reward_score == pytest.approx(0.625)
    assert candidate.initial_improvement_bonus == 0.5


def test_larger_initial_bonus_remains_capped_longer() -> None:
    approved_at = datetime.now(timezone.utc)
    candidate = RewardCandidate(uuid4(), "hk", 0.5, 1.0, approved_at)

    after_one_half_life = rank_reward_candidates(
        [candidate],
        observed_at=approved_at + timedelta(hours=72),
        bonus_half_life_hours=72,
        bonus_cap=0.5,
    )[0]
    after_two_half_lives = rank_reward_candidates(
        [candidate],
        observed_at=approved_at + timedelta(hours=144),
        bonus_half_life_hours=72,
        bonus_cap=0.5,
    )[0]

    assert after_one_half_life.current_improvement_bonus == pytest.approx(0.5)
    assert after_one_half_life.reward_score == pytest.approx(0.75)
    assert after_two_half_lives.current_improvement_bonus == pytest.approx(0.25)
    assert after_two_half_lives.reward_score == pytest.approx(0.625)
