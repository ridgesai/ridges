from typing import List

from fastapi import APIRouter

from models.evaluation_set import EvaluationSetProblem
from queries.evaluation_set import get_all_evaluation_set_problems_for_set_id, get_latest_set_id

router = APIRouter()


# /evaluation-sets/all-latest-set-problems
@router.get("/all-latest-set-problems")
async def evaluation_sets_all_latest_set_problems() -> List[EvaluationSetProblem]:
    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return []
    return await get_all_evaluation_set_problems_for_set_id(latest_set_id)
