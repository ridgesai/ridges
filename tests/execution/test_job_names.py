from uuid import UUID

from execution.engine import _format_job_name

RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def test_attempt_one_keeps_legacy_job_name():
    assert _format_job_name(problem_name="prob", evaluation_run_id=RUN_ID) == f"prob__{RUN_ID}"
    assert _format_job_name(problem_name="prob", evaluation_run_id=RUN_ID, attempt_number=1) == f"prob__{RUN_ID}"


def test_later_attempts_get_a_suffix():
    assert _format_job_name(problem_name="prob", evaluation_run_id=RUN_ID, attempt_number=2) == f"prob__{RUN_ID}__a2"
