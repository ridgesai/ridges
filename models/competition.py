from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints


class CompetitionState(str, Enum):
    ended = "ended"
    draft = "draft"
    paused = "paused"
    draining = "draining"
    open = "open"


def derive_competition_state(
    *,
    start_date: datetime | None,
    submissions_closed_at: datetime | None,
    is_paused: bool,
    end_date: datetime | None,
) -> CompetitionState:
    if end_date is not None:
        return CompetitionState.ended

    if start_date is None:
        return CompetitionState.draft

    if is_paused:
        return CompetitionState.paused

    if submissions_closed_at is not None:
        return CompetitionState.draining

    return CompetitionState.open


UnitInterval = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
OpenUnitInterval = Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
NonBlankStrictString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]


class CompetitionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scoring_mode: Literal["legacy", "consensus"]
    screener_1_threshold: UnitInterval
    screener_2_threshold: UnitInterval
    prune_threshold: UnitInterval
    required_validator_count: PositiveStrictInt
    pre_screening_enabled: StrictBool
    auto_approval_enabled: StrictBool
    hardcoding_policy_version: NonBlankStrictString
    incentive_enabled: StrictBool
    incentive_performance_threshold: OpenUnitInterval
    incentive_cost_threshold: OpenUnitInterval
    incentive_reward_half_life_hours: PositiveFiniteFloat
    incentive_time_multiplier_scale_hours: PositiveFiniteFloat
