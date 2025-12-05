import asyncio
import datetime

from typing import List
from fastapi import APIRouter
from pydantic import BaseModel
from utils.ttl import ttl_cache
from queries.problem_statistics import ProblemStatistics, get_problem_statistics
from queries.evaluation_set import get_latest_set_id, get_latest_set_created_at



router = APIRouter()



# /statistics/problem-statistics
class ProblemStatisticsResponse(BaseModel):
    problem_stats: List[ProblemStatistics]
    problem_set_id: int
    problem_set_created_at: datetime.datetime

@router.get("/problem-statistics")
@ttl_cache(ttl_seconds=15*60) # 15 mins
async def problem_statistics() -> ProblemStatisticsResponse:
    problem_stats, problem_set_id, problem_set_created_at = await asyncio.gather(
        get_problem_statistics(),
        get_latest_set_id(),
        get_latest_set_created_at()
    )

    return ProblemStatisticsResponse(
        problem_stats=problem_stats,
        problem_set_id=problem_set_id,
        problem_set_created_at=problem_set_created_at
    )