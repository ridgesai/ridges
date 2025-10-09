from typing import List

from fastapi import APIRouter

from api.queries.evaluation_set import get_all_problems_in_latest_set
from models.evaluation_set import EvaluationSetProblem

router = APIRouter()

@router.get("/latest-set-problems")
async def evaluation_sets_latest_set_problems() -> List[EvaluationSetProblem]:
    return await get_all_problems_in_latest_set()
