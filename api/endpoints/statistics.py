import asyncio
import datetime

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from utils.ttl import ttl_cache
from queries.evaluation_set import get_latest_set_id, get_latest_set_created_at
from queries.problem_statistics import ProblemStatistics, get_problem_statistics

EARLIEST_SET_ID_WITH_GOOD_DATA = 7 # set_id 6 and before had skipped test runs


router = APIRouter()



# /statistics/problem-statistics?set_id=
class ProblemStatisticsResponse(BaseModel):
    problem_stats: List[ProblemStatistics]
    problem_set_id: int
    problem_set_created_at: datetime.datetime

@router.get("/problem-statistics")
@ttl_cache(ttl_seconds=15*60) # 15 mins
async def problem_statistics(set_id: Optional[int] = None) -> ProblemStatisticsResponse:
    max_problem_set_id = await get_latest_set_id()
    if set_id is None:
        set_id = max_problem_set_id
    if set_id > max_problem_set_id:
        raise HTTPException(status_code=400, detail=f"Set ID {set_id} is greater than the latest set ID {max_problem_set_id}")
    if set_id < EARLIEST_SET_ID_WITH_GOOD_DATA:
        raise HTTPException(status_code=400, detail=f"Set ID {set_id} is before the earliest set ID with good data {EARLIEST_SET_ID_WITH_GOOD_DATA}")

    problem_stats, problem_set_created_at = await asyncio.gather(
        get_problem_statistics(set_id),
        get_latest_set_created_at()
    )
    return ProblemStatisticsResponse(
        problem_stats=problem_stats,
        problem_set_id=set_id,
        problem_set_created_at=problem_set_created_at
    )
