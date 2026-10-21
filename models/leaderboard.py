from enum import Enum

from pydantic import ConfigDict, Field, field_validator

from models.agent import AgentCompetitionStatus
from models.pagination import PageParams
from utils.validator_hotkeys import ValidatorName


class LeaderboardSortField(str, Enum):
    rank = "rank"
    agent_name = "name"
    score = "score"
    cost = "cost"
    runtime = "runtime"
    status = "status"
    submitted_at = "submitted_at"


class LeaderboardQuery(PageParams):
    """Query params for GET /evaluation-sets/{set_id}/leaderboard. Every filter is ANDed together"""

    model_config = ConfigDict(extra="forbid")

    sort: str = Field(
        LeaderboardSortField.rank.value,
        description=(
            f"One of {', '.join(field.value for field in LeaderboardSortField)}; prefix with '-' to sort descending"
        ),
    )
    search: str | None = Field(None, description="Case-insensitive substring match on the agent name")
    status: list["AgentCompetitionStatus"] = Field(default_factory=list)
    validator: list["ValidatorName"] = Field(default_factory=list)
    ranked: bool = False
    approved: bool = False

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, value: str) -> str:
        LeaderboardSortField(value.removeprefix("-"))
        return value

    @property
    def sort_field(self) -> LeaderboardSortField:
        return LeaderboardSortField(self.sort.removeprefix("-"))

    @property
    def sort_descending(self) -> bool:
        return self.sort.startswith("-")
