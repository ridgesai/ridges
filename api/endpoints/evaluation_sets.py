from typing import List

from fastapi import APIRouter

from models.evaluation_set import EvaluationSet, EvaluationSetProblem
from queries.evaluation_set import (
    get_all_evaluation_set_problems_for_set_id,
    get_all_evaluation_sets,
    get_latest_set_id,
)

router = APIRouter(tags=["evaluation-sets"])


@router.get("/")
async def evaluation_sets_list() -> list[EvaluationSet]:
    return await get_all_evaluation_sets()


# /evaluation-sets/all-latest-set-problems
@router.get("/all-latest-set-problems")
async def evaluation_sets_all_latest_set_problems() -> List[EvaluationSetProblem]:
    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return []
    return await get_all_evaluation_set_problems_for_set_id(latest_set_id)
