import asyncio
from typing import Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from api.competition_resolution import resolve_optional_public_competition
from api.incentives import get_current_allocations
from models.competition import CompetitionState
from models.evaluation_set import EvaluationSetGroup
from queries.competition import get_competition_policy
from queries.statistics import (
    get_average_score_per_evaluation_set_group,
    get_average_wait_time_per_evaluation_set_group,
)
from utils.ttl import ttl_cache

router = APIRouter()
CACHE_PAST_COMPETITION_TTL_SECONDS = 24 * 60 * 60


# /scoring/weights
@router.get("/weights")
async def weights() -> Dict[str, float]:
    allocations = await get_current_allocations()
    return allocations.hotkey_weights


# /scoring/screener-info
class ScoringScreenerInfoResponse(BaseModel):
    set_id: int
    screener_1_threshold: float | None
    screener_2_threshold: float | None
    prune_threshold: float | None
    incentive_performance_threshold: float | None
    incentive_cost_threshold: float | None

    screener_1_average_score: Optional[float] = None
    screener_2_average_score: Optional[float] = None
    validator_average_score: Optional[float] = None

    screener_1_average_wait_time: Optional[float] = None
    screener_2_average_wait_time: Optional[float] = None
    validator_average_wait_time: Optional[float] = None


async def _build_screener_info(set_id: int) -> ScoringScreenerInfoResponse:
    policy = await get_competition_policy(set_id)
    average_score_per_evaluation_set_group, average_wait_time_per_evaluation_set_group = await asyncio.gather(
        get_average_score_per_evaluation_set_group(set_id),
        get_average_wait_time_per_evaluation_set_group(
            set_id,
            None if policy is None else policy.required_validator_count,
        ),
    )

    return ScoringScreenerInfoResponse(
        set_id=set_id,
        screener_1_threshold=None if policy is None else policy.screener_1_threshold,
        screener_2_threshold=None if policy is None else policy.screener_2_threshold,
        prune_threshold=None if policy is None else policy.prune_threshold,
        incentive_performance_threshold=None if policy is None else policy.incentive_performance_threshold,
        incentive_cost_threshold=None if policy is None else policy.incentive_cost_threshold,
        screener_1_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.screener_1],
        screener_2_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.screener_2],
        validator_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.validator],
        screener_1_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.screener_1],
        screener_2_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.screener_2],
        validator_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.validator],
    )


_cached_live_screener_info = ttl_cache(ttl_seconds=60)(_build_screener_info)
_cached_past_screener_info = ttl_cache(ttl_seconds=CACHE_PAST_COMPETITION_TTL_SECONDS)(_build_screener_info)


@router.get("/screener-info")
async def screener_info(set_id: int | None = None) -> ScoringScreenerInfoResponse:
    competition = await resolve_optional_public_competition(set_id)
    builder = _cached_past_screener_info if competition.state is CompetitionState.ended else _cached_live_screener_info
    return await builder(competition.set_id)
