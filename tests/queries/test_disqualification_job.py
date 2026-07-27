from datetime import datetime, timezone
from uuid import uuid4

from models.disqualification_job import DisqualificationJob


def test_disqualification_job_model_roundtrips():
    now = datetime.now(timezone.utc)
    agent_id = uuid4()
    job_id = uuid4()
    job = DisqualificationJob(
        id=job_id,
        agent_id=agent_id,
        set_id=71,
        created_at=now,
        processed_at=None,
        attempts=0,
        error=None,
    )
    assert job.id == job_id
    assert job.agent_id == agent_id
    assert job.set_id == 71
    assert job.processed_at is None
    assert job.attempts == 0
    assert job.error is None
