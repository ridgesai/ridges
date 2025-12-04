from typing import List
from fastapi import APIRouter
from utils.ttl import ttl_cache
from queries.problem_statistics import ProblemStatistics, get_problem_statistics



router = APIRouter()



# /statistics/problem-statistics
@router.get("/problem-statistics")
@ttl_cache(ttl_seconds=5*60) # 5 minutes
async def problem_statistics() -> List[ProblemStatistics]:
    return await get_problem_statistics()