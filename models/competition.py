from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, localcontext
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)


class CompetitionState(str, Enum):
    ended = "ended"
    draft = "draft"
    paused = "paused"
    draining = "draining"
    open = "open"


class PublicCompetition(BaseModel):
    set_id: int
    name: str | None
    description: str | None
    links: list[str]
    state: CompetitionState
    accepting: bool
    processable: bool
    emission_active: bool
    created_at: datetime
    start_date: datetime
    submissions_closed_at: datetime | None
    emissions_end_at: datetime | None
    end_date: datetime | None
    raw_emission_weight: float


def exact_decimal_sum(values: Iterable[Decimal]) -> Decimal:
    items = list(values)
    if not items:
        return Decimal("0")

    integer_places = max([1, *(value.adjusted() + 1 for value in items if value)])
    fractional_places = max([0, *(-value.as_tuple().exponent for value in items)])
    with localcontext() as context:
        context.prec = integer_places + fractional_places + len(str(len(items))) + 2
        return sum(items, Decimal("0"))


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


def derive_competition_capabilities(
    *,
    state: CompetitionState,
    policy_complete: bool,
    raw_emission_weight: Decimal,
    emissions_end_at: datetime | None,
    observed_at: datetime,
) -> tuple[bool, bool, bool]:
    accepting = policy_complete and state is CompetitionState.open
    processable = policy_complete and state in {CompetitionState.open, CompetitionState.draining}
    emission_active = (
        processable and raw_emission_weight > 0 and (emissions_end_at is None or observed_at < emissions_end_at)
    )
    return accepting, processable, emission_active


UnitInterval = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
OpenUnitInterval = Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
NonBlankStrictString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]
AdminReason = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000),
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


class CompetitionStateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    started: StrictBool
    submissions_closed: StrictBool
    is_paused: StrictBool
    emissions_end_at: AwareDatetime | None
    ended: StrictBool
    reason: AdminReason

    @model_validator(mode="after")
    def require_paired_submission_cutoff(self) -> CompetitionStateUpdateRequest:
        if self.submissions_closed != (self.emissions_end_at is not None):
            raise ValueError("submissions_closed and emissions_end_at must be set or cleared together")
        return self


class CompetitionPolicyUpdateRequest(CompetitionPolicy):
    reason: AdminReason


class CompetitionMetadata(BaseModel):
    """Editorial fields that carry no lifecycle or scoring meaning."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)] | None
    description: (
        Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=5000)] | None
    )
    links: Annotated[list[AnyHttpUrl], Field(max_length=20)]

    @model_validator(mode="after")
    def reject_duplicate_links(self) -> CompetitionMetadata:
        rendered = [str(link) for link in self.links]
        if len(rendered) != len(set(rendered)):
            raise ValueError("links must not contain duplicates")
        return self


class CompetitionMetadataUpdateRequest(CompetitionMetadata):
    reason: AdminReason


RawEmissionWeight = Annotated[
    Decimal,
    Field(ge=Decimal("0"), le=Decimal("1"), allow_inf_nan=False),
]


class CompetitionAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_id: StrictInt
    raw_emission_weight: RawEmissionWeight


class CompetitionAllocationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allocations: list[CompetitionAllocation]
    reason: AdminReason

    @model_validator(mode="after")
    def validate_vector(self) -> CompetitionAllocationUpdateRequest:
        set_ids = [allocation.set_id for allocation in self.allocations]
        if len(set_ids) != len(set(set_ids)):
            raise ValueError("allocations must contain each set_id at most once")
        if exact_decimal_sum(allocation.raw_emission_weight for allocation in self.allocations) > 1:
            raise ValueError("raw emission weights must sum to at most 1")
        return self


class CompetitionAdminSnapshot(BaseModel):
    set_id: int
    name: str | None
    description: str | None
    links: list[str]
    state: CompetitionState
    started: bool
    start_date: datetime | None
    submissions_closed: bool
    submissions_closed_at: datetime | None
    is_paused: bool
    emissions_end_at: datetime | None
    ended: bool
    end_date: datetime | None
    raw_emission_weight: Decimal
    policy: CompetitionPolicy | None


class CompetitionAllocationSnapshot(BaseModel):
    allocations: list[CompetitionAllocation]
    owner_emission_weight: RawEmissionWeight
