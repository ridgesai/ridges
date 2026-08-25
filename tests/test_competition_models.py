from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from math import inf, nan

import pytest
from pydantic import ValidationError

from models.competition import (
    CompetitionAllocationUpdateRequest,
    CompetitionPolicy,
    CompetitionState,
    derive_competition_capabilities,
    derive_competition_state,
    exact_decimal_sum,
)

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)

VALID_POLICY = {
    "scoring_mode": "consensus",
    "screener_1_threshold": 0.4,
    "screener_2_threshold": 0.5,
    "prune_threshold": 0.9,
    "required_validator_count": 3,
    "pre_screening_enabled": True,
    "auto_approval_enabled": False,
    "hardcoding_policy_version": "hardcoding-v1",
    "incentive_enabled": True,
    "incentive_performance_threshold": 0.03,
    "incentive_cost_threshold": 0.06,
    "incentive_reward_half_life_hours": 336.0,
    "incentive_time_multiplier_scale_hours": 12.0,
}


@pytest.mark.parametrize(
    ("start_date", "submissions_closed_at", "is_paused", "end_date", "expected"),
    [
        (NOW, NOW, True, NOW, CompetitionState.ended),
        (None, NOW, True, None, CompetitionState.draft),
        (NOW, NOW, True, None, CompetitionState.paused),
        (NOW, NOW, False, None, CompetitionState.draining),
        (NOW, None, False, None, CompetitionState.open),
    ],
)
def test_derive_competition_state_and_precedence(
    start_date: datetime | None,
    submissions_closed_at: datetime | None,
    is_paused: bool,
    end_date: datetime | None,
    expected: CompetitionState,
) -> None:
    assert (
        derive_competition_state(
            start_date=start_date,
            submissions_closed_at=submissions_closed_at,
            is_paused=is_paused,
            end_date=end_date,
        )
        is expected
    )


@pytest.mark.parametrize(
    (
        "state",
        "policy_complete",
        "weight",
        "cutoff",
        "expected",
    ),
    [
        (CompetitionState.open, True, Decimal("0.5"), None, (True, True, True)),
        (CompetitionState.open, True, Decimal("0"), None, (True, True, False)),
        (CompetitionState.draining, True, Decimal("0.5"), NOW, (False, True, False)),
        (
            CompetitionState.draining,
            True,
            Decimal("0.5"),
            datetime(2026, 8, 20, 0, 0, 1, tzinfo=timezone.utc),
            (False, True, True),
        ),
        (CompetitionState.paused, True, Decimal("0.5"), None, (False, False, False)),
        (CompetitionState.ended, True, Decimal("0.5"), None, (False, False, False)),
        (CompetitionState.open, False, Decimal("0.5"), None, (False, False, False)),
    ],
)
def test_competition_capabilities_are_row_local_and_cutoff_is_strict(
    state: CompetitionState,
    policy_complete: bool,
    weight: Decimal,
    cutoff: datetime | None,
    expected: tuple[bool, bool, bool],
) -> None:
    assert (
        derive_competition_capabilities(
            state=state,
            policy_complete=policy_complete,
            raw_emission_weight=weight,
            emissions_end_at=cutoff,
            observed_at=NOW,
        )
        == expected
    )


def test_policy_is_strict_extra_forbid_and_round_trips() -> None:
    policy = CompetitionPolicy.model_validate({**VALID_POLICY, "hardcoding_policy_version": "  hardcoding-v1  "})

    assert policy.hardcoding_policy_version == "hardcoding-v1"
    assert "schema_version" not in policy.model_dump()
    assert CompetitionPolicy.model_validate_json(policy.model_dump_json()) == policy

    with pytest.raises(ValidationError, match="extra"):
        CompetitionPolicy.model_validate({**VALID_POLICY, "schema_version": 1})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scoring_mode", "weighted"),
        ("screener_1_threshold", "0.4"),
        ("screener_2_threshold", True),
        ("required_validator_count", 3.0),
        ("required_validator_count", True),
        ("pre_screening_enabled", 1),
        ("auto_approval_enabled", "false"),
        ("hardcoding_policy_version", 1),
        ("incentive_enabled", "true"),
    ],
)
def test_policy_rejects_wrong_or_coerced_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CompetitionPolicy.model_validate({**VALID_POLICY, field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("screener_1_threshold", -0.01),
        ("screener_1_threshold", 1.01),
        ("screener_2_threshold", inf),
        ("prune_threshold", nan),
        ("required_validator_count", 0),
        ("incentive_performance_threshold", 0.0),
        ("incentive_performance_threshold", 1.0),
        ("incentive_performance_threshold", inf),
        ("incentive_cost_threshold", 0.0),
        ("incentive_cost_threshold", 1.0),
        ("incentive_cost_threshold", nan),
        ("incentive_reward_half_life_hours", 0.0),
        ("incentive_reward_half_life_hours", inf),
        ("incentive_time_multiplier_scale_hours", -1.0),
        ("incentive_time_multiplier_scale_hours", nan),
    ],
)
def test_policy_rejects_out_of_range_and_nonfinite_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CompetitionPolicy.model_validate({**VALID_POLICY, field: value})


def test_policy_accepts_closed_threshold_boundaries() -> None:
    policy = CompetitionPolicy.model_validate(
        {
            **VALID_POLICY,
            "screener_1_threshold": 0.0,
            "screener_2_threshold": 1.0,
            "prune_threshold": 0.0,
        }
    )

    assert policy.screener_1_threshold == 0.0
    assert policy.screener_2_threshold == 1.0
    assert policy.prune_threshold == 0.0


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_policy_rejects_blank_hardcoding_policy_version(value: str) -> None:
    with pytest.raises(ValidationError):
        CompetitionPolicy.model_validate({**VALID_POLICY, "hardcoding_policy_version": value})


def test_allocation_model_rejects_a_microscopically_overallocated_vector() -> None:
    with pytest.raises(ValidationError, match="sum to at most 1"):
        CompetitionAllocationUpdateRequest(
            allocations=[
                {"set_id": 1, "raw_emission_weight": Decimal("1")},
                {"set_id": 2, "raw_emission_weight": Decimal("1e-400")},
            ],
            reason="invalid",
        )


def test_exact_decimal_sum_preserves_empty_and_microscopic_values() -> None:
    assert exact_decimal_sum([]) == Decimal("0")
    assert exact_decimal_sum([Decimal("1"), Decimal("1e-400")]) > 1
