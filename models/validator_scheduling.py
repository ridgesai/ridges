from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.competition import AdminReason

ValidatorSchedulingMode = Literal["disabled", "normal", "prioritized"]
CompetitionId = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class CompetitionSchedulingSnapshot(BaseModel):
    set_id: int
    mode: ValidatorSchedulingMode


class CompetitionSchedulingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: ValidatorSchedulingMode
    reason: AdminReason


class ValidatorAllowlistSnapshot(BaseModel):
    validator_hotkey: str
    # None means unrestricted; [] means no new validator evaluations.
    allowed_set_ids: list[int] | None


class ValidatorAllowlistUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allowed_set_ids: list[CompetitionId] | None
    reason: AdminReason

    @model_validator(mode="after")
    def validate_unique_ids(self):
        if self.allowed_set_ids is not None and len(set(self.allowed_set_ids)) != len(self.allowed_set_ids):
            raise ValueError("allowed_set_ids must not contain duplicates")
        return self
