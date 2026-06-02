import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from models.agent import AgentStatus


def _r4(v: float) -> float:
    return round(float(v), 4)


def _r2(v: float) -> float:
    return round(float(v), 2)


Float4 = Annotated[float, AfterValidator(_r4)]
Float2 = Annotated[float, AfterValidator(_r2)]


class EvaluationSetGroup(str, Enum):
    screener_1 = "screener_1"
    screener_2 = "screener_2"
    validator = "validator"

    @staticmethod
    def from_validator_hotkey(validator_hotkey: str) -> "EvaluationSetGroup":
        if validator_hotkey.startswith("screener-1"):
            return EvaluationSetGroup.screener_1
        elif validator_hotkey.startswith("screener-2"):
            return EvaluationSetGroup.screener_2
        else:
            return EvaluationSetGroup.validator


class EvaluationSet(BaseModel):
    id: int = Field(validation_alias="set_id")
    created_at: datetime.datetime
    competition_name: str | None = None
    competition_start_date: datetime.datetime | None = None
    competition_end_date: datetime.datetime | None = None


class EvaluationSetProblem(BaseModel):
    set_id: int
    set_group: EvaluationSetGroup
    problem_name: str
    benchmark_family: str | None = None
    problem_suite_name: str | None = None
    execution_spec: dict[str, Any] | None = None
    created_at: datetime.datetime


class NewEvaluationSetProblem(BaseModel):
    set_group: EvaluationSetGroup
    problem_name: str
    benchmark_family: str
    problem_suite_name: str | None = None
    execution_spec: dict[str, Any]


class EvaluationSetDetailPipelineStage(BaseModel):
    """Detailed information about a specific stage in the evaluation pipeline, including the stage name, how many agents passed that stage, and the pass rate for that stage."""

    stage: str
    count: int
    pass_rate: Float4


class EvaluationSetDetailSubmissions(BaseModel):
    """Detailed submission statistics for an evaluation set, including total agents, unique miners, hardcoded rejection rate, and a breakdown of how many agents passed each stage of the evaluation pipeline."""

    total_agents: int
    unique_miners: int
    hardcoded_rejection_rate: Float4
    approved_emission_count: int
    pipeline: list[EvaluationSetDetailPipelineStage]


class EvaluationSetDetailBenchmarkThreshold(BaseModel):
    """Information about a specific benchmark threshold, including the threshold value and how many agents exceeded it"""

    threshold: int
    agents_above: int


class EvaluationSetDetailScores(BaseModel):
    """Detailed score information for an evaluation set, including the best score, average score, and how many agents exceeded certain benchmark thresholds."""

    best: Float2 | None
    average: Float2 | None
    benchmark_thresholds: list[EvaluationSetDetailBenchmarkThreshold]


class EvaluationSetDetailVsPreviousSet(BaseModel):
    """Information about how the current evaluation set compares to the previous one, including the delta of the top score and how many agents beat the previous best score."""

    top_score_delta: str
    agents_beating_previous_best: int


class EvaluationSetDetailTopAgent(BaseModel):
    agent_id: UUID
    name: str
    version_num: int
    final_score: Float4
    emission: float | None = None


class EvaluationSetDetailEfficiency(BaseModel):
    lowest_average_cost_usd_top_agents: Float4 | None
    lowest_average_runtime_seconds_top_agents: Float4 | None
    average_agent_cost_usd: Float4 | None
    average_agent_runtime_seconds: Float4 | None


class EvaluationSetDetailLeaderboardAgent(BaseModel):
    rank: int | None
    agent_id: UUID
    miner_hotkey: str
    name: str
    version_num: int
    status: AgentStatus
    approved: bool
    average_cost_usd: Float4 | None
    average_runtime_seconds: Float4 | None
    validator_hotkeys: list[str]
    created_at: datetime.datetime
    is_benchmark_agent: bool
    benchmark_description: str | None


class EvaluationSetDetail(BaseModel):
    """Detailed information about an evaluation set, including submission statistics, scores, and comparison to the previous set."""

    id: int
    competition_name: str | None
    competition_start_date: datetime.datetime | None
    competition_end_date: datetime.datetime | None
    submissions: EvaluationSetDetailSubmissions
    scores: EvaluationSetDetailScores
    vs_previous_set: EvaluationSetDetailVsPreviousSet | None
    top_agent: EvaluationSetDetailTopAgent | None
    efficiency: EvaluationSetDetailEfficiency


class ApprovedAgent(BaseModel):
    id: UUID
    miner_hotkey: str
    name: str
    version_num: int
    created_at: datetime.datetime
    final_score: Float4
    emission: float
