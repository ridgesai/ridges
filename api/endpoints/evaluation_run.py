from uuid import UUID

from fastapi import APIRouter, HTTPException

from models.evaluation_run import EvaluationRun, EvaluationRunLogType
from queries.evaluation_run import (
    get_evaluation_run_by_id,
    get_evaluation_run_logs_by_id,
)
from utils.problem_alias import add_test_aliases, make_problem_alias

router = APIRouter()


def _temp_evaluation_run_metrics(run: EvaluationRun) -> dict:
    run_time_seconds = None
    if run.started_initializing_agent_at is not None and run.finished_or_errored_at is not None:
        run_time_seconds = (run.finished_or_errored_at - run.started_initializing_agent_at).total_seconds()

    return {
        "run_time_seconds": run_time_seconds,
        "run_cost_usd": None,
        "problem_total_runs": 0,
        "problem_average_time_seconds": None,
        "problem_average_cost_usd": None,
    }


# /evaluation-run/get-by-id?evaluation_run_id=
@router.get("/get-by-id")
async def evaluation_run_get_by_id(evaluation_run_id: UUID) -> EvaluationRun:
    evaluation_run = await get_evaluation_run_by_id(evaluation_run_id)

    if evaluation_run is None:
        raise HTTPException(status_code=404, detail=f"Evaluation run with ID {evaluation_run_id} does not exist.")

    alias = make_problem_alias(evaluation_run.problem_name, evaluation_run.benchmark_family)
    test_results = add_test_aliases(
        evaluation_run.test_results,
        problem_name=evaluation_run.problem_name,
        benchmark_family=evaluation_run.benchmark_family,
    )

    return evaluation_run.model_copy(
        update={
            "problem_alias": alias,
            "test_results": test_results,
            **_temp_evaluation_run_metrics(evaluation_run),
        }
    )


# /evaluation-run/get-logs-by-id?evaluation_run_id=&type=
@router.get("/get-logs-by-id")
async def evaluation_run_get_logs_by_id(evaluation_run_id: UUID, type: EvaluationRunLogType) -> str:
    logs = await get_evaluation_run_logs_by_id(evaluation_run_id, type)

    if logs is None:
        raise HTTPException(
            status_code=404, detail=f"Evaluation run logs with ID {evaluation_run_id} and type {type} do not exist."
        )

    return logs
