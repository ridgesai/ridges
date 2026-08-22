from datetime import datetime, timezone
from uuid import uuid4

import pytest

from queries.approval import project_next_approval_job_state


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


class _FakeConn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.conn = self
        self.fetch_queries: list[str] = []
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query: str, *args):
        self.fetch_queries.append(query)
        if not self.rows:
            return None
        return self.rows.pop(0)

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


def _job_row(*, status: str, verdict: str, agent_id=None, set_id: int = 7):
    return {
        "job_id": uuid4(),
        "agent_id": agent_id or uuid4(),
        "set_id": set_id,
        "status": status,
        "aggregate_verdict": verdict,
        "aggregate_score": 0.82,
        "aggregate_confidence": 0.74,
        "aggregate_summary": "summary",
        "created_at": datetime.now(timezone.utc),
    }


def _policy_row(*, incentive_enabled: bool = False):
    return {
        "scoring_mode": "consensus",
        "screener_1_threshold": 0.4,
        "screener_2_threshold": 0.4,
        "prune_threshold": 0.4,
        "required_validator_count": 3,
        "pre_screening_enabled": True,
        "auto_approval_enabled": True,
        "hardcoding_policy_version": "hardcoding-v1",
        "incentive_enabled": incentive_enabled,
        "incentive_performance_threshold": 0.03,
        "incentive_cost_threshold": 0.06,
        "incentive_reward_half_life_hours": 336.0,
        "incentive_time_multiplier_scale_hours": 12.0,
    }


@pytest.mark.anyio
async def test_projector_projects_needs_review_without_published_fields() -> None:
    conn = _FakeConn([_job_row(status="needs_review", verdict="needs_review"), _policy_row()])

    projected = await project_next_approval_job_state.__wrapped__(conn)

    assert projected is True
    assert "projected_at IS NULL" in conn.fetch_queries[0]
    upsert_args = conn.executed[0][1]
    assert upsert_args[3] == "needs_review"
    assert upsert_args[4] == "needs_review"
    assert upsert_args[8] is None
    assert upsert_args[9] is None
    assert not any("INSERT INTO approved_agents" in query for query, _args in conn.executed)


@pytest.mark.anyio
async def test_projector_projects_completed_approved_job_into_approved_agents() -> None:
    conn = _FakeConn([_job_row(status="completed", verdict="approved"), _policy_row()])

    projected = await project_next_approval_job_state.__wrapped__(conn)

    assert projected is True
    upsert_args = conn.executed[0][1]
    assert upsert_args[3] == "completed"
    assert upsert_args[4] == "approved"
    assert upsert_args[8] == "approved"
    assert upsert_args[9] == 0.82
    assert any("INSERT INTO approved_agents" in query for query, _args in conn.executed)


@pytest.mark.anyio
async def test_projector_leaves_job_unprojected_without_competition_policy() -> None:
    conn = _FakeConn([_job_row(status="completed", verdict="approved"), None])

    projected = await project_next_approval_job_state.__wrapped__(conn)

    assert projected is False
    assert conn.executed == []
