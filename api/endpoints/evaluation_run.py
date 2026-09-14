from uuid import UUID

from fastapi import APIRouter, HTTPException

from models.evaluation_run import PublicEvaluationRunDetail
from queries.evaluation_run import (
    get_evaluation_run_by_id,
    get_evaluation_run_metrics_by_id,
)
from utils.public_view import to_public_run_detail

router = APIRouter()


# /evaluation-run/get-by-id?evaluation_run_id=
@router.get("/get-by-id")
async def evaluation_run_get_by_id(evaluation_run_id: UUID) -> PublicEvaluationRunDetail:
    evaluation_run = await get_evaluation_run_by_id(evaluation_run_id)

    if evaluation_run is None:
        raise HTTPException(status_code=404, detail=f"Evaluation run with ID {evaluation_run_id} does not exist.")

    metrics = await get_evaluation_run_metrics_by_id(evaluation_run_id)

    return to_public_run_detail(evaluation_run, metrics)
