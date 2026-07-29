from datetime import datetime, timezone
from uuid import uuid4

from models.disqualified_problem import DisqualifiedProblem
from models.evaluation_set import EvaluationSetGroup
from models.problem_disqualification_job import ProblemDisqualificationJob


def test_disqualified_problem_model_roundtrips():
    now = datetime.now(timezone.utc)
    p = DisqualifiedProblem(
        set_id=71,
        set_group=EvaluationSetGroup.validator,
        problem_name="flaky_task",
        reason="flaky harness",
        created_at=now,
    )
    assert p.set_id == 71
    assert p.set_group == EvaluationSetGroup.validator
    assert p.problem_name == "flaky_task"


def test_problem_disqualification_job_model_roundtrips():
    now = datetime.now(timezone.utc)
    job_id = uuid4()
    job = ProblemDisqualificationJob(
        id=job_id,
        set_id=71,
        set_group=EvaluationSetGroup.validator,
        problem_name="flaky_task",
        created_at=now,
        processed_at=None,
        attempts=0,
        error=None,
    )
    assert job.id == job_id
    assert job.set_group == EvaluationSetGroup.validator
    assert job.attempts == 0
