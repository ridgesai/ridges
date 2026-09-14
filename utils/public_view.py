from typing import Any, Mapping

from models.evaluation_run import (
    EvaluationRun,
    PublicEvaluationRun,
    PublicEvaluationRunDetail,
)
from models.problem import ProblemTestResult, PublicProblemTestResult
from utils.problem_alias import make_problem_alias, make_test_alias


def to_public_test_results(
    test_results: list[ProblemTestResult] | None,
    *,
    problem_name: str,
    benchmark_family: str | None,
) -> list[PublicProblemTestResult] | None:
    if test_results is None:
        return None

    return [
        PublicProblemTestResult(
            test_alias=make_test_alias(
                benchmark_family=benchmark_family,
                problem_name=problem_name,
                test_name=test_result.name,
                test_category=test_result.category.value,
            ),
            category=test_result.category,
            status=test_result.status,
        )
        for test_result in test_results
    ]


def _public_run_fields(evaluation_run: EvaluationRun) -> dict[str, Any]:
    return {
        "evaluation_run_id": evaluation_run.evaluation_run_id,
        "evaluation_id": evaluation_run.evaluation_id,
        "problem_alias": make_problem_alias(evaluation_run.problem_name, evaluation_run.benchmark_family),
        "benchmark_family": evaluation_run.benchmark_family,
        "status": evaluation_run.status,
        "test_results": to_public_test_results(
            evaluation_run.test_results,
            problem_name=evaluation_run.problem_name,
            benchmark_family=evaluation_run.benchmark_family,
        ),
        "verifier_reward": evaluation_run.verifier_reward,
        "error_code": evaluation_run.error_code,
        "error_message": evaluation_run.error_code.message if evaluation_run.error_code else None,
        "cost_usd": evaluation_run.cost_usd,
        "created_at": evaluation_run.created_at,
        "started_initializing_agent_at": evaluation_run.started_initializing_agent_at,
        "started_running_agent_at": evaluation_run.started_running_agent_at,
        "started_initializing_eval_at": evaluation_run.started_initializing_eval_at,
        "started_running_eval_at": evaluation_run.started_running_eval_at,
        "finished_or_errored_at": evaluation_run.finished_or_errored_at,
    }


def to_public_run(evaluation_run: EvaluationRun) -> PublicEvaluationRun:
    return PublicEvaluationRun(**_public_run_fields(evaluation_run))


def to_public_run_detail(
    evaluation_run: EvaluationRun,
    metrics: Mapping[str, Any] | None = None,
) -> PublicEvaluationRunDetail:
    return PublicEvaluationRunDetail(**_public_run_fields(evaluation_run), **(metrics or {}))
