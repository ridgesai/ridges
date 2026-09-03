from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import numpy as np
import pytest

from api import incentives
from api.endpoints import scoring as scoring_endpoint
from models.competition import CompetitionPolicy
from queries.scores import (
    CompetitionWeightInput,
    LegacyWeightReceiver,
    WeightCalculationSnapshot,
)
from utils.bittensor import HotkeySubnetInfo
from utils.incentives import RewardCandidate

OBSERVED_AT = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _policy(*, incentive_enabled: bool, required_validator_count: int = 3, half_life: float = 336) -> CompetitionPolicy:
    return CompetitionPolicy(
        scoring_mode="consensus",
        screener_1_threshold=0.3,
        screener_2_threshold=0.4,
        prune_threshold=0.9,
        required_validator_count=required_validator_count,
        pre_screening_enabled=True,
        auto_approval_enabled=True,
        hardcoding_policy_version="hardcoding-v1",
        incentive_enabled=incentive_enabled,
        incentive_performance_threshold=0.03,
        incentive_cost_threshold=0.06,
        incentive_reward_half_life_hours=half_life,
        incentive_time_multiplier_scale_hours=12,
    )


def _candidate(
    hotkey: str,
    reward_score: float,
    *,
    approved_at: datetime = OBSERVED_AT,
) -> RewardCandidate:
    return RewardCandidate(
        agent_id=uuid4(),
        miner_hotkey=hotkey,
        initial_reward_score=reward_score,
        approved_at=approved_at,
    )


def _competition(
    set_id: int,
    raw_weight: str,
    *,
    incentive_enabled: bool = True,
    candidates: tuple[RewardCandidate, ...] = (),
    receiver: LegacyWeightReceiver | None = None,
    start_date: datetime | None = OBSERVED_AT - timedelta(days=1),
    is_paused: bool = False,
    emissions_end_at: datetime | None = None,
    end_date: datetime | None = None,
    policy: CompetitionPolicy | None = None,
) -> CompetitionWeightInput:
    return CompetitionWeightInput(
        set_id=set_id,
        start_date=start_date,
        is_paused=is_paused,
        emissions_end_at=emissions_end_at,
        end_date=end_date,
        raw_emission_weight=Decimal(raw_weight),
        policy=policy if policy is not None else _policy(incentive_enabled=incentive_enabled),
        legacy_receiver=receiver,
        incentive_candidates=candidates,
    )


def _snapshot(*competitions: CompetitionWeightInput) -> WeightCalculationSnapshot:
    return WeightCalculationSnapshot(observed_at=OBSERVED_AT, competitions=competitions)


def _registered(*hotkeys: str) -> dict[str, HotkeySubnetInfo]:
    return {hotkey: HotkeySubnetInfo(uid=uid, emission=0.0) for uid, hotkey in enumerate(hotkeys)}


def test_n1_incentive_normalizes_registered_candidates_and_preserves_agent_contributions() -> None:
    first = _candidate("shared", 1)
    second = _candidate("shared", 2)
    third = _candidate("other", 3)
    unregistered = _candidate("missing", 100)

    result = incentives.calculate_current_allocations(
        _snapshot(_competition(1, "1", candidates=(unregistered, first, second, third))),
        _registered("shared", "other"),
    )

    assert result.hotkey_weights == pytest.approx({"shared": 0.5, "other": 0.5})
    assert result.agent_weights == pytest.approx({first.agent_id: 1 / 6, second.agent_id: 2 / 6, third.agent_id: 3 / 6})
    assert unregistered.agent_id not in result.agent_weights


def test_n1_legacy_preserves_the_single_recent_receiver() -> None:
    receiver = LegacyWeightReceiver(agent_id=uuid4(), miner_hotkey="leader")

    result = incentives.calculate_current_allocations(
        _snapshot(_competition(1, "1", incentive_enabled=False, receiver=receiver)),
        _registered("leader"),
    )

    assert result == incentives.CurrentAllocations(
        hotkey_weights={"leader": 1.0},
        agent_weights={receiver.agent_id: 1.0},
    )


def test_stored_reward_half_life_controls_each_competition_decay() -> None:
    old = _candidate("old", 1, approved_at=OBSERVED_AT - timedelta(hours=1))
    recent = _candidate("recent", 1)
    competition = _competition(1, "1", candidates=(old, recent), policy=_policy(incentive_enabled=True, half_life=1))

    result = incentives.calculate_current_allocations(
        _snapshot(competition),
        _registered("old", "recent"),
    )

    assert result.hotkey_weights == pytest.approx({"recent": 2 / 3, "old": 1 / 3})


@pytest.mark.parametrize(
    "competitions",
    [
        (
            _competition(1, "0.9", candidates=(_candidate("first", 3), _candidate("second", 1))),
            _competition(
                2,
                "0.1",
                incentive_enabled=False,
                receiver=LegacyWeightReceiver(agent_id=uuid4(), miner_hotkey="legacy"),
            ),
        ),
        (
            _competition(1, "0.5", candidates=(_candidate("first", 1),)),
            _competition(2, "0.5", candidates=(_candidate("second", 1),)),
        ),
    ],
)
def test_n2_raw_shares_are_absolute_and_each_competition_normalizes_independently(competitions) -> None:
    result = incentives.calculate_current_allocations(
        _snapshot(*competitions),
        _registered("first", "second", "legacy"),
    )

    if competitions[0].raw_emission_weight == Decimal("0.9"):
        assert result.hotkey_weights == pytest.approx({"first": 0.675, "second": 0.225, "legacy": 0.1})
    else:
        assert result.hotkey_weights == pytest.approx({"first": 0.5, "second": 0.5})
    assert sum(result.hotkey_weights.values()) == pytest.approx(1)


def test_same_hotkey_aggregates_across_competitions_without_final_renormalization() -> None:
    result = incentives.calculate_current_allocations(
        _snapshot(
            _competition(1, "0.25", candidates=(_candidate("shared", 1),)),
            _competition(2, "0.5", candidates=(_candidate("shared", 1),)),
        ),
        _registered("shared", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == pytest.approx({"shared": 0.75, incentives.config.OWNER_HOTKEY: 0.25})


@pytest.mark.parametrize(
    "updates",
    [
        {"start_date": None},
        {"is_paused": True},
        {"end_date": OBSERVED_AT},
        {"emissions_end_at": OBSERVED_AT - timedelta(microseconds=1)},
        {"emissions_end_at": OBSERVED_AT},
    ],
)
def test_inactive_lifecycle_and_exact_cutoff_route_the_raw_share_to_owner(updates) -> None:
    competition = _competition(1, "0.4", candidates=(_candidate("candidate", 1),), **updates)

    result = incentives.calculate_current_allocations(
        _snapshot(competition),
        _registered("candidate", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == {incentives.config.OWNER_HOTKEY: 1.0}
    assert result.agent_weights == {}


def test_draining_competition_emits_strictly_before_cutoff() -> None:
    competition = _competition(
        1,
        "0.4",
        candidates=(_candidate("candidate", 1),),
        emissions_end_at=OBSERVED_AT + timedelta(microseconds=1),
    )

    result = incentives.calculate_current_allocations(
        _snapshot(competition),
        _registered("candidate", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == pytest.approx({"candidate": 0.4, incentives.config.OWNER_HOTKEY: 0.6})


@pytest.mark.parametrize(
    "competition",
    [
        _competition(1, "0.4"),
        _competition(1, "0.4", candidates=(_candidate("missing", 1),)),
        _competition(1, "0.4", candidates=(_candidate("registered", 0),)),
    ],
)
def test_empty_unregistered_and_zero_candidates_route_to_owner(competition) -> None:
    result = incentives.calculate_current_allocations(
        _snapshot(competition),
        _registered("registered", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == {incentives.config.OWNER_HOTKEY: 1.0}
    assert result.agent_weights == {}


@pytest.mark.parametrize(
    "competition",
    [
        _competition(1, "1e-400"),
        _competition(1, "1e-400", candidates=(_candidate("registered", 0),)),
        _competition(1, "1e-400", candidates=(_candidate("missing", 1),)),
    ],
)
def test_microscopic_empty_zero_and_unregistered_shares_route_to_owner(competition) -> None:
    result = incentives.calculate_current_allocations(
        _snapshot(competition),
        _registered("registered", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == {incentives.config.OWNER_HOTKEY: 1.0}
    assert result.agent_weights == {}


def test_microscopic_registered_candidate_share_fails_weight_arithmetic() -> None:
    with pytest.raises(ValueError, match="must remain finite and positive in weight arithmetic"):
        incentives.calculate_current_allocations(
            _snapshot(_competition(1, "1e-400", candidates=(_candidate("registered", 1),))),
            _registered("registered", incentives.config.OWNER_HOTKEY),
        )


def test_registered_positive_distribution_cannot_fully_underflow_after_multiplication() -> None:
    with pytest.raises(ValueError, match="positive distribution that underflows"):
        incentives.calculate_current_allocations(
            _snapshot(
                _competition(
                    1,
                    "5e-324",
                    candidates=(_candidate("first", 1), _candidate("second", 1)),
                )
            ),
            _registered("first", "second", incentives.config.OWNER_HOTKEY),
        )


def test_registered_owner_tail_may_underflow_when_main_candidate_vector_is_valid() -> None:
    microscopic = "1e-400"
    main_share = "0." + ("9" * 400)
    snapshot = _snapshot(
        _competition(1, main_share, candidates=(_candidate("candidate", 1),)),
        _competition(2, microscopic),
    )

    result = incentives.calculate_current_allocations(
        snapshot,
        _registered("candidate", incentives.config.OWNER_HOTKEY),
    )

    assert result.hotkey_weights == {"candidate": 1.0}
    with pytest.raises(ValueError, match="owner hotkey is not registered"):
        incentives.calculate_current_allocations(snapshot, _registered("candidate"))


def test_microscopic_raw_weight_cannot_hide_an_overallocated_vector() -> None:
    with pytest.raises(ValueError, match="total no greater than one"):
        incentives.calculate_current_allocations(
            _snapshot(
                _competition(1, "1", candidates=(_candidate("candidate", 1),)),
                _competition(2, "1e-400"),
            ),
            _registered("candidate", incentives.config.OWNER_HOTKEY),
        )


def test_no_competitions_routes_the_complete_vector_to_registered_owner() -> None:
    result = incentives.calculate_current_allocations(
        _snapshot(),
        _registered(incentives.config.OWNER_HOTKEY),
    )

    assert result == incentives.CurrentAllocations(
        hotkey_weights={incentives.config.OWNER_HOTKEY: 1.0},
        agent_weights={},
    )


def test_owner_registration_is_required_only_when_owner_receives_weight() -> None:
    full = _competition(1, "1", candidates=(_candidate("candidate", 1),))
    assert incentives.calculate_current_allocations(_snapshot(full), _registered("candidate")).hotkey_weights == {
        "candidate": 1.0
    }

    with pytest.raises(ValueError, match="owner hotkey is not registered"):
        incentives.calculate_current_allocations(
            _snapshot(_competition(1, "0.9", candidates=(_candidate("candidate", 1),))),
            _registered("candidate"),
        )


@pytest.mark.parametrize("reward", [float("nan"), float("inf"), -1])
def test_corrupt_active_reward_data_fails_the_whole_calculation(reward) -> None:
    with pytest.raises(ValueError, match="invalid reward score"):
        incentives.calculate_current_allocations(
            _snapshot(_competition(1, "1", candidates=(_candidate("candidate", reward),))),
            _registered("candidate"),
        )


def test_active_positive_competition_requires_complete_policy() -> None:
    competition = _competition(1, "1", candidates=(_candidate("candidate", 1),))
    competition = replace(competition, policy=None)
    with pytest.raises(ValueError, match="no complete policy"):
        incentives.calculate_current_allocations(_snapshot(competition), _registered("candidate"))


def test_source_provenance_rejects_a_competition_hidden_by_same_hotkey_aggregation() -> None:
    zero_tail = float(np.nextafter(np.float32(0.5 / 65535), np.float32(0)))
    tiny = Decimal(str(zero_tail))
    large = Decimal("1") - tiny
    shared = "shared"

    with pytest.raises(ValueError, match="Competition 2 has no contribution"):
        incentives.calculate_current_allocations(
            _snapshot(
                _competition(
                    1,
                    str(large),
                    incentive_enabled=False,
                    receiver=LegacyWeightReceiver(agent_id=uuid4(), miner_hotkey=shared),
                ),
                _competition(
                    2,
                    str(tiny),
                    incentive_enabled=False,
                    receiver=LegacyWeightReceiver(agent_id=uuid4(), miner_hotkey=shared),
                ),
            ),
            _registered(shared),
        )


def test_exact_pinned_float32_round_to_even_boundary_and_zero_tail_behavior() -> None:
    tie = np.float32(0.5 / 65535)
    below = float(np.nextafter(tie, np.float32(0)))
    above = float(np.nextafter(tie, np.float32(1)))

    with pytest.raises(ValueError, match="no contribution"):
        incentives._validate_source_representability(set_id=1, contributions={"tail": below}, final_maximum=1.0)
    with pytest.raises(ValueError, match="no contribution"):
        incentives._validate_source_representability(
            set_id=1,
            contributions={"tail": float(tie)},
            final_maximum=1.0,
        )
    incentives._validate_source_representability(
        set_id=1,
        contributions={"tail": above},
        final_maximum=1.0,
    )


def test_individual_zero_encoded_tail_is_allowed_when_its_competition_survives() -> None:
    below = float(np.nextafter(np.float32(0.5 / 65535), np.float32(0)))

    incentives._validate_source_representability(
        set_id=1,
        contributions={"main": 0.1, "tail": below},
        final_maximum=0.9,
    )


@pytest.mark.anyio
async def test_each_calculation_uses_one_uncached_platform_metagraph(monkeypatch) -> None:
    snapshot = _snapshot(_competition(1, "1", candidates=(_candidate("candidate", 1),)))
    snapshot_calls = 0

    async def load_snapshot():
        return snapshot

    async def get_subnet_info():
        nonlocal snapshot_calls
        snapshot_calls += 1
        return _registered("candidate")

    monkeypatch.setattr(incentives, "get_weight_calculation_snapshot", load_snapshot)
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)

    first = await incentives.get_current_allocations()
    second = await incentives.get_current_allocations()

    assert second == first
    assert snapshot_calls == 2


@pytest.mark.anyio
async def test_public_presentation_metagraph_wrapper_remains_cached(monkeypatch) -> None:
    calls = 0

    async def get_subnet_info():
        nonlocal calls
        calls += 1
        return _registered("candidate")

    incentives.get_subnet_hotkey_info.cache_clear()
    monkeypatch.setattr(incentives.subtensor_client, "get_subnet_hotkey_info", get_subnet_info)
    try:
        assert await incentives.get_subnet_hotkey_info() == await incentives.get_subnet_hotkey_info()
        assert calls == 1
    finally:
        incentives.get_subnet_hotkey_info.cache_clear()


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
