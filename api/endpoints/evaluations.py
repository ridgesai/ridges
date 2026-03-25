from uuid import UUID

from fastapi import APIRouter, HTTPException

from models.evaluation import HydratedEvaluation
from queries.evaluation import get_hydrated_evaluation_by_evaluation_run_id

router = APIRouter()


# /evaluations/get-by-evaluation-run-id?evaluation_run_id=
@router.get("/get-by-evaluation-run-id")
async def evaluations_get_by_evaluation_run_id(evaluation_run_id: UUID) -> HydratedEvaluation:
    evaluation = await get_hydrated_evaluation_by_evaluation_run_id(evaluation_run_id)

    if evaluation is None:
        raise HTTPException(
            status_code=404, detail=f"Evaluation with evaluation run ID {evaluation_run_id} does not exist."
        )

    return evaluation
