"""Tests for models/ — Pydantic model validation for core domain objects."""

import pytest
from uuid import uuid4
from datetime import datetime

from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunErrorCode


class TestEvaluationRunStatus:
    """Tests for EvaluationRunStatus enum."""

    def test_all_statuses_exist(self):
        expected = {
            "pending", "initializing_agent", "running_agent",
            "initializing_eval", "running_eval", "finished", "error"
        }
        actual = {s.value for s in EvaluationRunStatus}
        assert expected.issubset(actual)

    def test_status_values_are_strings(self):
        for status in EvaluationRunStatus:
            assert isinstance(status.value, str)


class TestEvaluationRunErrorCode:
    """Tests for EvaluationRunErrorCode enum."""

    def test_validator_internal_error_exists(self):
        assert hasattr(EvaluationRunErrorCode, "VALIDATOR_INTERNAL_ERROR")

    def test_validator_unknown_problem_exists(self):
        assert hasattr(EvaluationRunErrorCode, "VALIDATOR_UNKNOWN_PROBLEM")

    def test_get_error_message_returns_string(self):
        for code in EvaluationRunErrorCode:
            msg = code.get_error_message()
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_error_code_categories(self):
        assert EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT.is_agent_error()
        assert not EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT.is_validator_error()
        assert EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.is_validator_error()
        assert not EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.is_agent_error()
        assert EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING.is_platform_error()
        assert not EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING.is_agent_error()

    def test_all_agent_errors_in_1xxx_range(self):
        for code in EvaluationRunErrorCode:
            if code.is_agent_error():
                assert 1000 <= code.value < 2000

    def test_all_validator_errors_in_2xxx_range(self):
        for code in EvaluationRunErrorCode:
            if code.is_validator_error():
                assert 2000 <= code.value < 3000

    def test_all_platform_errors_in_3xxx_range(self):
        for code in EvaluationRunErrorCode:
            if code.is_platform_error():
                assert 3000 <= code.value < 4000


class TestEvaluationRun:
    """Tests for EvaluationRun model."""

    def test_minimal_creation(self):
        run = EvaluationRun(
            evaluation_run_id=uuid4(),
            evaluation_id=uuid4(),
            problem_name="test-problem",
            status=EvaluationRunStatus.pending,
            created_at=datetime.now(),
        )
        assert run.status == EvaluationRunStatus.pending
        assert run.patch is None
        assert run.error_code is None

    def test_finished_run_with_results(self):
        from models.problem import ProblemTestResult, ProblemTestResultStatus
        run = EvaluationRun(
            evaluation_run_id=uuid4(),
            evaluation_id=uuid4(),
            problem_name="test-problem",
            status=EvaluationRunStatus.finished,
            patch="--- a/file.py\n+++ b/file.py\n",
            test_results=[
                ProblemTestResult(name="test1", category="default", status=ProblemTestResultStatus.PASS),
            ],
            created_at=datetime.now(),
            finished_or_errored_at=datetime.now(),
        )
        assert run.status == EvaluationRunStatus.finished
        assert len(run.test_results) == 1

    def test_error_run(self):
        run = EvaluationRun(
            evaluation_run_id=uuid4(),
            evaluation_id=uuid4(),
            problem_name="test-problem",
            status=EvaluationRunStatus.error,
            error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
            error_message="Something went wrong",
            created_at=datetime.now(),
        )
        assert run.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
