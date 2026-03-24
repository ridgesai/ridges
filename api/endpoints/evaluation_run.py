from uuid import UUID

from fastapi import APIRouter, HTTPException

from models.evaluation_run import EvaluationRun, EvaluationRunLogType
from queries.evaluation_run import get_evaluation_run_by_id, get_evaluation_run_logs_by_id

router = APIRouter()


# /evaluation-run/get-by-id?evaluation_run_id=
@router.get("/get-by-id")
async def evaluation_run_get_by_id(evaluation_run_id: UUID) -> EvaluationRun:
    evaluation_run = await get_evaluation_run_by_id(evaluation_run_id)

    if evaluation_run is None:
        raise HTTPException(status_code=404, detail=f"Evaluation run with ID {evaluation_run_id} does not exist.")

    return evaluation_run


# /evaluation-run/get-logs-by-id?evaluation_run_id=&type=
@router.get("/get-logs-by-id")
async def evaluation_run_get_logs_by_id(evaluation_run_id: UUID, type: EvaluationRunLogType) -> str:
    logs = await get_evaluation_run_logs_by_id(evaluation_run_id, type)

    if logs is None:
        raise HTTPException(
            status_code=404, detail=f"Evaluation run logs with ID {evaluation_run_id} and type {type} do not exist."
        )

    return logs
