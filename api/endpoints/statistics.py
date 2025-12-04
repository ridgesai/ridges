import datetime
from fastapi import APIRouter
from utils.ttl import ttl_cache
from queries.problem_statistics import ProblemInfo, get_problem_statistics, get_latest_set_id, get_latest_set_created_at
from pydantic import BaseModel
import asyncio



router = APIRouter()



class ProblemStatisticsResponse(BaseModel):
    problem_stats: list[ProblemInfo]
    problem_set_id: int
    problem_set_created_at: datetime.datetime

# /statistics/problem-statistics
@router.get("/problem-statistics")
@ttl_cache(ttl_seconds=900) # 15 mins
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
