from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.endpoints import evaluation_run as evaluation_run_endpoint
from api.endpoints import retrieval as retrieval_endpoint
from models.evaluation import Evaluation, PublicEvaluationWithRuns
from models.evaluation_run import (
    EvaluationRun,
    EvaluationRunErrorCode,
    EvaluationRunStatus,
    PublicEvaluationRun,
)
from models.evaluation_set import EvaluationSetGroup
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus
from utils.public_view import to_public_run, to_public_run_detail

SECRET_PROBLEM_NAME = "pg-chatwoot-notification-retention-cutoff-isolation-001"
SECRET_TEST_NAME = "chatwoot.notification_retention_cutoff_isolation::verifier_integrity"
SECRET_PATCH = "diff --git a/app/jobs/secret.rb b/app/jobs/secret.rb"
SECRET_S3_KEY = "tasks/db-challenges/ch-secret-001/sha256_deadbeef.tar.gz"
SECRET_TRACEBACK = "Traceback (most recent call last): RuntimeError at /tests/source_guard.rb:85"
SECRET_TASK_DIGEST = "sha256:a17590e5b4066eabb9983cccbd1b5bc491c6161fedcf55adc5b5241e03a5a9b4"

SECRETS = (
    SECRET_PROBLEM_NAME,
    SECRET_TEST_NAME,
    SECRET_PATCH,
    SECRET_S3_KEY,
    SECRET_TRACEBACK,
    SECRET_TASK_DIGEST,
)


def _internal_run() -> EvaluationRun:
    now = datetime.now(timezone.utc)
    return EvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=uuid4(),
        problem_name=SECRET_PROBLEM_NAME,
        benchmark_family="ridges",
        execution_spec={"s3_key": SECRET_S3_KEY, "task_digest": SECRET_TASK_DIGEST},
        status=EvaluationRunStatus.finished,
        patch=SECRET_PATCH,
        test_results=[
            ProblemTestResult(
                name=SECRET_TEST_NAME,
                category=ProblemTestCategory.fail_to_pass,
                status=ProblemTestResultStatus.FAIL,
            )
        ],
        verifier_reward=0.0,
        error_message=SECRET_TRACEBACK,
        cost_usd=0.0081,
        created_at=now,
    )


def _assert_no_secrets(payload: str) -> None:
    for secret in SECRETS:
        assert secret not in payload, f"public payload leaked: {secret}"


def test_public_run_omits_problem_identity_and_solution() -> None:
    public_run = to_public_run(_internal_run())

    _assert_no_secrets(public_run.model_dump_json())

    assert public_run.problem_alias
    assert public_run.test_results is not None
    assert public_run.test_results[0].test_alias
    assert public_run.test_results[0].status is ProblemTestResultStatus.FAIL


def test_public_run_detail_omits_secrets_and_keeps_metrics() -> None:
    detail = to_public_run_detail(
        _internal_run(),
        {"run_time_seconds": 12.5, "problem_total_runs": 3, "attempt_count": 2},
    )

    _assert_no_secrets(detail.model_dump_json())

    assert detail.run_time_seconds == 12.5
    assert detail.problem_total_runs == 3
    assert detail.attempt_count == 2


def test_public_evaluation_with_runs_omits_secrets() -> None:
    now = datetime.now(timezone.utc)
    payload = PublicEvaluationWithRuns(
        evaluation_id=uuid4(),
        agent_id=uuid4(),
        validator_hotkey="5Fhotkey",
        set_id=28,
        evaluation_set_group=EvaluationSetGroup.validator,
        created_at=now,
        runs=[to_public_run(_internal_run())],
    )

    _assert_no_secrets(payload.model_dump_json())


def test_public_run_model_declares_no_secret_fields() -> None:
    """A secret added to the internal model must not reach the public one by inheritance."""
    forbidden = {"problem_name", "patch", "execution_spec"}

    assert forbidden.isdisjoint(PublicEvaluationRun.model_fields)
    assert not issubclass(PublicEvaluationRun, EvaluationRun)


@pytest.mark.anyio
async def test_evaluations_for_agent_endpoint_omits_secrets(monkeypatch) -> None:
    evaluation = Evaluation(
        evaluation_id=uuid4(),
        agent_id=uuid4(),
        validator_hotkey="5Fhotkey",
        set_id=28,
        evaluation_set_group=EvaluationSetGroup.validator,
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get_evaluations_for_agent_id(agent_id):
        return [evaluation]

    async def fake_get_all_evaluation_runs_in_evaluation_id(evaluation_id):
        return [_internal_run()]

    monkeypatch.setattr(retrieval_endpoint, "get_evaluations_for_agent_id", fake_get_evaluations_for_agent_id)
    monkeypatch.setattr(
        retrieval_endpoint,
        "get_all_evaluation_runs_in_evaluation_id",
        fake_get_all_evaluation_runs_in_evaluation_id,
    )

    response = await retrieval_endpoint.evaluations_for_agent(evaluation.agent_id)

    _assert_no_secrets(json.dumps([json.loads(item.model_dump_json()) for item in response]))
    assert response[0].runs[0].problem_alias


@pytest.mark.anyio
async def test_evaluation_run_get_by_id_endpoint_omits_secrets(monkeypatch) -> None:
    run = _internal_run()

    async def fake_get_evaluation_run_by_id(_evaluation_run_id):
        return run

    async def fake_get_evaluation_run_metrics_by_id(_evaluation_run_id):
        return {"run_time_seconds": 12.5, "problem_total_runs": 3}

    monkeypatch.setattr(evaluation_run_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)
    monkeypatch.setattr(
        evaluation_run_endpoint, "get_evaluation_run_metrics_by_id", fake_get_evaluation_run_metrics_by_id
    )

    response = await evaluation_run_endpoint.evaluation_run_get_by_id(run.evaluation_run_id)

    _assert_no_secrets(response.model_dump_json())
    assert response.run_time_seconds == 12.5


AGENT_CRASH_MESSAGE = (
    "The agent did not return a patch or raised an exception while being run: RuntimeError: "
    "agent_main() returned an empty patch at /installed-agent/ridges_miner_runtime.py"
)


def test_agent_fault_run_reports_only_the_enum_message() -> None:
    """Agent crash text is authored inside the sandbox, so only the curated enum text ships."""
    run = _internal_run().model_copy(
        update={
            "error_code": EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT,
            "error_message": AGENT_CRASH_MESSAGE,
        }
    )

    public_run = to_public_run(run)

    _assert_no_secrets(public_run.model_dump_json())
    assert AGENT_CRASH_MESSAGE not in public_run.model_dump_json()
    assert public_run.error_message == "The agent did not return a patch or raised an exception while being run"


def test_validator_fault_run_reports_only_the_enum_message() -> None:
    """2xxx stored messages interpolate problem_name and task digests, so they never ship."""
    run = _internal_run().model_copy(update={"error_code": EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR})

    public_run = to_public_run(run)

    _assert_no_secrets(public_run.model_dump_json())
    assert public_run.error_message == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.message


def test_unknown_problem_error_does_not_leak_the_problem_name() -> None:
    """VALIDATOR_UNKNOWN_PROBLEM builds its message straight from problem_name."""
    run = _internal_run().model_copy(
        update={
            "error_code": EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM,
            "error_message": f"The active evaluation set item '{SECRET_PROBLEM_NAME}' has no execution spec",
        }
    )

    _assert_no_secrets(to_public_run(run).model_dump_json())


def test_public_evaluation_schema_is_pinned() -> None:
    """PublicEvaluationWithRuns inherits Evaluation, so a field added there would go public."""
    assert set(PublicEvaluationWithRuns.model_fields) == {
        "evaluation_id",
        "agent_id",
        "validator_hotkey",
        "set_id",
        "evaluation_set_group",
        "created_at",
        "finished_at",
        "runs",
    }


def test_no_public_route_enumerates_evaluation_set_problems() -> None:
    """The held-out set must not be listable; a route ending in /problems would expose every problem_name."""
    from api.endpoints import evaluation_sets as evaluation_sets_endpoint

    paths = sorted(route.path for route in evaluation_sets_endpoint.router.routes)

    assert not [path for path in paths if path.endswith("/problems")], paths
