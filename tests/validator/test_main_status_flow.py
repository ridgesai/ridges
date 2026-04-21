from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import validator.main as validator_main
from execution.errors import EvaluationRunException
from execution.types import ExecutionResult
from models.evaluation_run import EvaluationRunErrorCode, EvaluationRunStatus
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus


def _evaluation_run(*, problem_name: str = "update-status-file", execution_spec: dict | None = None):
    return SimpleNamespace(
        evaluation_run_id=uuid4(),
        problem_name=problem_name,
        execution_spec=execution_spec or {},
    )


def _test_result(name: str, status: ProblemTestResultStatus) -> ProblemTestResult:
    return ProblemTestResult(name=name, category=ProblemTestCategory.default, status=status)


def _install_update_capture(monkeypatch):
    updates: list[tuple[EvaluationRunStatus, dict]] = []

    async def fake_update(evaluation_run_id, problem_name, updated_status, extra=None):
        updates.append((updated_status, dict(extra or {})))

    monkeypatch.setattr(validator_main, "update_evaluation_run", fake_update)
    return updates


def _set_common_globals(monkeypatch, *, log_size_limit: int = 100_000) -> None:
    monkeypatch.setattr(validator_main, "max_evaluation_run_log_size_bytes", log_size_limit)


@pytest.mark.anyio
async def test_run_evaluation_run_success_posts_expected_status_sequence(monkeypatch) -> None:
    updates = _install_update_capture(monkeypatch)
    _set_common_globals(monkeypatch)

    class FakeEngine:
        async def evaluate(self, **kwargs):
            return ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=1.0,
                test_results=[
                    _test_result("pass_case", ProblemTestResultStatus.PASS),
                    _test_result("skip_case", ProblemTestResultStatus.SKIP),
                ],
                agent_logs="agent log line",
                eval_logs="eval log line",
            )

    monkeypatch.setattr(validator_main, "execution_engine", FakeEngine())

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert [status for status, _ in updates] == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.initializing_eval,
        EvaluationRunStatus.running_eval,
        EvaluationRunStatus.finished,
    ]
    assert updates[2][1] == {"patch": "PATCH", "agent_logs": "agent log line"}
    assert updates[4][1] == {
        "verifier_reward": 1.0,
        "test_results": [
            {"name": "pass_case", "category": "default", "status": "pass"},
            {"name": "skip_case", "category": "default", "status": "skip"},
        ],
        "eval_logs": "eval log line",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_preserves_evaluation_run_exception(monkeypatch) -> None:
    updates = _install_update_capture(monkeypatch)
    _set_common_globals(monkeypatch)

    class FakeEngine:
        async def evaluate(self, **kwargs):
            raise EvaluationRunException(
                EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                "The agent returned an invalid patch",
                extra={"agent_logs": "agent logs", "eval_logs": "eval logs"},
            )

    monkeypatch.setattr(validator_main, "execution_engine", FakeEngine())

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert [status for status, _ in updates] == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.error,
    ]
    assert updates[-1][1] == {
        "error_code": EvaluationRunErrorCode.AGENT_INVALID_PATCH.value,
        "error_message": "The agent returned an invalid patch",
        "agent_logs": "agent logs",
        "eval_logs": "eval logs",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_wraps_unexpected_exception_as_validator_internal_error(monkeypatch) -> None:
    updates = _install_update_capture(monkeypatch)
    _set_common_globals(monkeypatch)

    class FakeEngine:
        async def evaluate(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(validator_main, "execution_engine", FakeEngine())

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert [status for status, _ in updates] == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.error,
    ]
    assert updates[-1][1]["error_code"] == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value
    assert "boom" in updates[-1][1]["error_message"]
    assert "Traceback:" in updates[-1][1]["error_message"]


@pytest.mark.anyio
async def test_run_evaluation_run_truncates_logs_on_success(monkeypatch) -> None:
    updates = _install_update_capture(monkeypatch)
    _set_common_globals(monkeypatch, log_size_limit=5)

    class FakeEngine:
        async def evaluate(self, **kwargs):
            return ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=1.0,
                test_results=[_test_result("pass_case", ProblemTestResultStatus.PASS)],
                agent_logs="abcdefghij",
                eval_logs="klmnopqrst",
            )

    monkeypatch.setattr(validator_main, "execution_engine", FakeEngine())

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert updates[2][1]["agent_logs"] == "<truncated 5 chars>\n\nfghij"
    assert updates[4][1]["eval_logs"] == "<truncated 5 chars>\n\npqrst"


@pytest.mark.anyio
async def test_run_evaluation_run_does_not_enter_eval_stages_if_backend_fails(monkeypatch) -> None:
    updates = _install_update_capture(monkeypatch)
    _set_common_globals(monkeypatch, log_size_limit=5)

    class FakeEngine:
        async def evaluate(self, **kwargs):
            raise EvaluationRunException(
                EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                "The agent returned an invalid patch",
                extra={"agent_logs": "abcdefghij", "eval_logs": "klmnopqrst"},
            )

    monkeypatch.setattr(validator_main, "execution_engine", FakeEngine())

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    statuses = [status for status, _ in updates]
    assert statuses == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.error,
    ]
    assert EvaluationRunStatus.initializing_eval not in statuses
    assert EvaluationRunStatus.running_eval not in statuses
    assert updates[-1][1]["agent_logs"] == "<truncated 5 chars>\n\nfghij"
    assert updates[-1][1]["eval_logs"] == "<truncated 5 chars>\n\npqrst"
