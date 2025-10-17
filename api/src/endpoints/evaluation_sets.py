from typing import List
from fastapi import APIRouter
from models.evaluation_set import EvaluationSetProblem
from queries.evaluation_set import get_all_evaluation_set_problems_in_latest_set_id



router = APIRouter()



@router.get("/all-latest-set-problems")
async def evaluation_sets_all_latest_set_problems() -> List[EvaluationSetProblem]:
    return await get_all_evaluation_set_problems_in_latest_set_id()