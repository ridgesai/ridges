from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import validator.main as validator_main
from api.endpoints.validator_models import (
    ValidatorRequestEvaluationResponseEvaluationRun,
    ValidatorUpdateEvaluationRunResponse,
)
from execution.errors import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode, EvaluationRunStatus

pytestmark = pytest.mark.anyio


def _fake_result(job_dir=None):
    return SimpleNamespace(
        backend="harbor",
        job_dir=job_dir,
        patch="a patch",
        agent_logs="agent logs",
        eval_logs="eval logs",
        verifier_reward=1.0,
        test_results=[],
        cost_usd=0.1,
    )


class FakeEngine:
    """Fails the first attempt with a retryable validator error, succeeds on the second."""

    def __init__(self):
        self.calls: list[int] = []

    async def evaluate(self, **kwargs):
        self.calls.append(kwargs.get("attempt_number", 1))
        if len(self.calls) == 1:
            raise EvaluationRunException(EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR, "transient failure")
        return _fake_result()


@pytest.fixture
def run_stub():
    return ValidatorRequestEvaluationResponseEvaluationRun(
        evaluation_run_id=uuid4(), problem_name="prob-1", execution_spec=None
    )


async def test_retry_directive_reruns_the_attempt(monkeypatch, run_stub):
    engine = FakeEngine()
    monkeypatch.setattr(validator_main, "execution_engine", engine)
    monkeypatch.setattr(validator_main, "max_evaluation_run_log_size_bytes", 1_000_000)

    updates: list[EvaluationRunStatus] = []

    async def fake_update_evaluation_run(evaluation_run_id, problem_name, updated_status, extra=None, *, timeout=None):
        updates.append(updated_status)
        if updated_status == EvaluationRunStatus.error:
            return ValidatorUpdateEvaluationRunResponse(
                retry=True, attempt_number=2, artifact_upload_url="https://s3.example.com/fresh"
            )
        return ValidatorUpdateEvaluationRunResponse()

    monkeypatch.setattr(validator_main, "update_evaluation_run", fake_update_evaluation_run)

    await validator_main._run_evaluation_run(run_stub, "agent code")

    assert engine.calls == [1, 2]  # second attempt carried attempt_number=2
    assert updates.count(EvaluationRunStatus.error) == 1
    assert updates.count(EvaluationRunStatus.finished) == 1
    # Two attempts each start by moving pending -> initializing_agent.
    assert updates.count(EvaluationRunStatus.initializing_agent) == 2


async def test_denied_retry_stops_after_one_attempt(monkeypatch, run_stub):
    class AlwaysFailingEngine:
        def __init__(self):
            self.calls = 0

        async def evaluate(self, **kwargs):
            self.calls += 1
            raise EvaluationRunException(EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR, "persistent failure")

    engine = AlwaysFailingEngine()
    monkeypatch.setattr(validator_main, "execution_engine", engine)
    monkeypatch.setattr(validator_main, "max_evaluation_run_log_size_bytes", 1_000_000)

    async def fake_update_evaluation_run(evaluation_run_id, problem_name, updated_status, extra=None, *, timeout=None):
        return ValidatorUpdateEvaluationRunResponse()  # retry always False

    monkeypatch.setattr(validator_main, "update_evaluation_run", fake_update_evaluation_run)

    await validator_main._run_evaluation_run(run_stub, "agent code")

    assert engine.calls == 1
