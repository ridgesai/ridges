from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.endpoints import evaluation_run as evaluation_run_endpoint
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus
from utils.problem_alias import make_problem_alias, make_test_alias


@pytest.mark.anyio
async def test_evaluation_run_get_by_id_adds_test_aliases(monkeypatch) -> None:
    monkeypatch.delenv("PROBLEM_ALIAS_SALT", raising=False)
    evaluation_run_id = uuid4()
    evaluation_run = EvaluationRun(
        evaluation_run_id=evaluation_run_id,
        evaluation_id=uuid4(),
        problem_name="acronym-py",
        benchmark_family="polyglot_py",
        status=EvaluationRunStatus.finished,
        test_results=[
            ProblemTestResult(
                name="task_tests.AcronymTests.test_ruby_on_rails",
                category=ProblemTestCategory.default,
                status=ProblemTestResultStatus.PASS,
            )
        ],
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get_evaluation_run_by_id(_evaluation_run_id):
        return evaluation_run

    async def fake_get_evaluation_run_metrics_by_id(_evaluation_run_id):
        return {}

    monkeypatch.setattr(evaluation_run_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)
    monkeypatch.setattr(
        evaluation_run_endpoint,
        "get_evaluation_run_metrics_by_id",
        fake_get_evaluation_run_metrics_by_id,
    )

    response = await evaluation_run_endpoint.evaluation_run_get_by_id(evaluation_run_id)

    assert response.problem_alias == make_problem_alias("acronym-py", "polyglot_py")
    assert response.test_results is not None
    assert response.test_results[0].test_alias == make_test_alias(
        benchmark_family="polyglot_py",
        problem_name="acronym-py",
        test_name="task_tests.AcronymTests.test_ruby_on_rails",
        test_category="default",
    )
    assert response.test_results[0].test_alias == "VALID-MODULE-A56"
    assert evaluation_run.test_results is not None
    assert evaluation_run.test_results[0].test_alias is None
