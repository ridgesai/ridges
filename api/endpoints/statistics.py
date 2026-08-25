import asyncio
import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from queries.competition import (
    PublicEvaluationSetContext,
    get_public_evaluation_set_context,
    resolve_compatibility_competition_set_id,
)
from queries.evaluation_set import get_set_created_at
from queries.problem_statistics import ProblemStatistics, get_problem_statistics
from utils.ttl import ttl_cache

router = APIRouter()
CACHE_PAST_COMPETITION_TTL_SECONDS = 24 * 60 * 60
CACHE_LIVE_COMPETITION_TTL_SECONDS = 15 * 60


# /statistics/problem-statistics?set_id=
class ProblemStatisticsResponse(BaseModel):
    problem_stats: List[ProblemStatistics]
    problem_set_id: int
    problem_set_created_at: datetime.datetime


async def _build_problem_statistics(set_id: int) -> ProblemStatisticsResponse:
    problem_stats, problem_set_created_at = await asyncio.gather(
        get_problem_statistics(set_id), get_set_created_at(set_id)
    )

    return ProblemStatisticsResponse(
        problem_stats=problem_stats, problem_set_id=set_id, problem_set_created_at=problem_set_created_at
    )


_cached_live_problem_statistics = ttl_cache(ttl_seconds=CACHE_LIVE_COMPETITION_TTL_SECONDS)(_build_problem_statistics)
_cached_past_problem_statistics = ttl_cache(ttl_seconds=CACHE_PAST_COMPETITION_TTL_SECONDS)(_build_problem_statistics)


async def _resolve_optional_public_evaluation_set(set_id: int | None) -> PublicEvaluationSetContext:
    if set_id is None or set_id == -1:
        resolved_set_id = await resolve_compatibility_competition_set_id()
    else:
        resolved_set_id = set_id
    context = None if resolved_set_id is None else await get_public_evaluation_set_context(resolved_set_id)
    if context is None:
        raise HTTPException(status_code=404, detail="No live competition found")
    return context


@router.get("/problem-statistics")
async def problem_statistics(set_id: Optional[int] = None) -> ProblemStatisticsResponse:
    context = await _resolve_optional_public_evaluation_set(set_id)
    builder = _cached_past_problem_statistics if context.use_historical_cache else _cached_live_problem_statistics
    return await builder(context.set_id)
