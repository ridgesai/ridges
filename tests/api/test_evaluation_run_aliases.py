from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    started_at = datetime.now(timezone.utc)
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
        started_initializing_agent_at=started_at,
        finished_or_errored_at=started_at + timedelta(seconds=12.5),
    )

    async def fake_get_evaluation_run_by_id(_evaluation_run_id):
        return evaluation_run

    monkeypatch.setattr(evaluation_run_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)

    response = await evaluation_run_endpoint.evaluation_run_get_by_id(evaluation_run_id)

    assert response.problem_alias == make_problem_alias("acronym-py", "polyglot_py")
    assert response.run_time_seconds == 12.5
    assert response.run_cost_usd is None
    assert response.problem_total_runs == 0
    assert response.problem_average_time_seconds is None
    assert response.problem_average_cost_usd is None
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
