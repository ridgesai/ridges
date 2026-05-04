from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import validator.main as validator_main
from execution.errors import EvaluationRunException
from execution.types import ExecutionResult, TrialSnapshot
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


def _status_names(updates: list[dict]) -> list[EvaluationRunStatus]:
    return [update["status"] for update in updates]


def _install_update_capture(
    monkeypatch,
    *,
    failure_counts: dict[EvaluationRunStatus, int] | None = None,
):
    capture = {
        "updates": [],
    }
    remaining_failures = dict(failure_counts or {})

    async def fake_update(evaluation_run_id, problem_name, updated_status, extra=None, *, timeout=None):
        if timeout == validator_main.STATUS_HOOK_TIMEOUT_SECONDS and remaining_failures.get(updated_status, 0) > 0:
            remaining_failures[updated_status] -= 1
            raise httpx.ConnectTimeout(f"timeout posting {updated_status.value}")

        capture["updates"].append(
            {
                "status": updated_status,
                "extra": dict(extra or {}),
                "timeout": timeout,
                "evaluation_run_id": evaluation_run_id,
                "problem_name": problem_name,
            }
        )

    monkeypatch.setattr(validator_main, "update_evaluation_run", fake_update)
    return capture


def _set_common_globals(monkeypatch, *, log_size_limit: int = 100_000) -> None:
    monkeypatch.setattr(validator_main, "max_evaluation_run_log_size_bytes", log_size_limit)


class HookAwareEngine:
    def __init__(
        self,
        *,
        result: ExecutionResult | None = None,
        exception: BaseException | None = None,
        trigger_agent_started: bool = True,
        trigger_verification_started: bool = True,
        snapshot: TrialSnapshot | None = None,
    ) -> None:
        self.result = result
        self.exception = exception
        self.trigger_agent_started = trigger_agent_started
        self.trigger_verification_started = trigger_verification_started
        self.snapshot = snapshot or TrialSnapshot(patch="PATCH", agent_logs="agent log line")

    async def evaluate(self, **kwargs):
        if self.trigger_agent_started:
            try:
                await kwargs["on_agent_started"]()
            except Exception:
                pass

        if self.trigger_verification_started:
            try:
                await kwargs["on_verification_started"](self.snapshot)
            except Exception:
                pass

        if self.exception is not None:
            raise self.exception

        assert self.result is not None
        return self.result


@pytest.mark.anyio
async def test_run_evaluation_run_success_posts_expected_status_sequence(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            result=ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=1.0,
                test_results=[
                    _test_result("pass_case", ProblemTestResultStatus.PASS),
                    _test_result("skip_case", ProblemTestResultStatus.SKIP),
                ],
                agent_logs="agent log line",
                eval_logs="eval log line",
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.initializing_eval,
        EvaluationRunStatus.running_eval,
        EvaluationRunStatus.finished,
    ]
    assert updates[1]["timeout"] == validator_main.STATUS_HOOK_TIMEOUT_SECONDS
    assert updates[2]["extra"] == {"patch": "PATCH", "agent_logs": "agent log line"}
    assert updates[2]["timeout"] == validator_main.STATUS_HOOK_TIMEOUT_SECONDS
    assert updates[4]["extra"] == {
        "patch": "PATCH",
        "agent_logs": "agent log line",
        "verifier_reward": 1.0,
        "test_results": [
            {"name": "pass_case", "category": "default", "status": "pass"},
            {"name": "skip_case", "category": "default", "status": "skip"},
        ],
        "eval_logs": "eval log line",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_posts_below_one_reward_as_finished(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            result=ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=0.8,
                test_results=[_test_result("fail_case", ProblemTestResultStatus.FAIL)],
                agent_logs="agent log line",
                eval_logs="eval log line",
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.initializing_eval,
        EvaluationRunStatus.running_eval,
        EvaluationRunStatus.finished,
    ]
    assert updates[-1]["extra"] == {
        "patch": "PATCH",
        "agent_logs": "agent log line",
        "verifier_reward": 0.8,
        "test_results": [{"name": "fail_case", "category": "default", "status": "fail"}],
        "eval_logs": "eval log line",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_still_finishes_when_hook_posts_time_out(monkeypatch) -> None:
    capture = _install_update_capture(
        monkeypatch,
        failure_counts={
            EvaluationRunStatus.running_agent: 1,
            EvaluationRunStatus.initializing_eval: 1,
            EvaluationRunStatus.running_eval: 1,
        },
    )
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            result=ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=1.0,
                test_results=[_test_result("pass_case", ProblemTestResultStatus.PASS)],
                agent_logs="agent log line",
                eval_logs="eval log line",
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.finished,
    ]
    assert updates[-1]["extra"] == {
        "patch": "PATCH",
        "agent_logs": "agent log line",
        "verifier_reward": 1.0,
        "test_results": [{"name": "pass_case", "category": "default", "status": "pass"}],
        "eval_logs": "eval log line",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_preserves_eval_phase_on_verifier_failure(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            exception=EvaluationRunException(
                EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_EVAL,
                "Verifier failed after patch was produced",
                extra={"agent_logs": "agent logs", "eval_logs": "eval logs"},
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.running_agent,
        EvaluationRunStatus.initializing_eval,
        EvaluationRunStatus.running_eval,
        EvaluationRunStatus.error,
    ]
    assert updates[-1]["extra"] == {
        "error_code": EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_EVAL.value,
        "error_message": "Verifier failed after patch was produced",
        "agent_logs": "agent logs",
        "eval_logs": "eval logs",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_preserves_evaluation_run_exception_before_agent_start(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            trigger_agent_started=False,
            trigger_verification_started=False,
            exception=EvaluationRunException(
                EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                "The agent returned an invalid patch",
                extra={"agent_logs": "agent logs", "eval_logs": "eval logs"},
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.error,
    ]
    assert updates[-1]["extra"] == {
        "error_code": EvaluationRunErrorCode.AGENT_INVALID_PATCH.value,
        "error_message": "The agent returned an invalid patch",
        "agent_logs": "agent logs",
        "eval_logs": "eval logs",
    }


@pytest.mark.anyio
async def test_run_evaluation_run_truncates_logs_on_success(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch, log_size_limit=5)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            snapshot=TrialSnapshot(patch="PATCH", agent_logs="abcdefghij"),
            result=ExecutionResult(
                backend="harbor",
                patch="PATCH",
                verifier_reward=1.0,
                test_results=[_test_result("pass_case", ProblemTestResultStatus.PASS)],
                agent_logs="abcdefghij",
                eval_logs="klmnopqrst",
            ),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert updates[2]["extra"]["agent_logs"] == "<truncated 5 chars>\n\nfghij"
    assert updates[4]["extra"]["agent_logs"] == "<truncated 5 chars>\n\nfghij"
    assert updates[4]["extra"]["eval_logs"] == "<truncated 5 chars>\n\npqrst"


@pytest.mark.anyio
async def test_run_evaluation_run_wraps_unexpected_exception_as_validator_internal_error(monkeypatch) -> None:
    capture = _install_update_capture(monkeypatch)
    updates = capture["updates"]
    _set_common_globals(monkeypatch)
    monkeypatch.setattr(
        validator_main,
        "execution_engine",
        HookAwareEngine(
            trigger_agent_started=False,
            trigger_verification_started=False,
            exception=RuntimeError("boom"),
        ),
    )

    await validator_main._run_evaluation_run(_evaluation_run(), "print('agent')")

    assert _status_names(updates) == [
        EvaluationRunStatus.initializing_agent,
        EvaluationRunStatus.error,
    ]
    assert updates[-1]["extra"]["error_code"] == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value
    assert "boom" in updates[-1]["extra"]["error_message"]
    assert "Traceback:" in updates[-1]["extra"]["error_message"]
