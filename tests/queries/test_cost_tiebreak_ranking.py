from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from models.agent import AgentStatus
from queries import agent as agent_queries
from queries import scores as score_queries


class _FetchRowConn:
    def __init__(self, row):
        self.row = row
        self.query: str | None = None

    async def fetchrow(self, query: str):
        self.query = query
        return self.row


class _FetchConn:
    def __init__(self, rows):
        self.rows = rows
        self.query: str | None = None
        self.args: tuple = ()

    async def fetch(self, query: str, *args):
        self.query = query
        self.args = args
        return self.rows


def _agent_score_row():
    return {
        "agent_id": uuid4(),
        "miner_hotkey": "miner-hotkey",
        "name": "Agent",
        "version_num": 0,
        "status": AgentStatus.finished.value,
        "created_at": datetime.now(timezone.utc),
        "ip_address": None,
        "set_id": 21,
        "approved": True,
        "validator_count": 3,
        "final_score": 0.75,
        "approval_review_status": None,
    }


def _assert_cost_tiebreak_query(query: str) -> None:
    assert "avg(" in query.lower()
    assert "avg_cost_usd) as avg_cost_usd" in query.lower()
    assert "avg_cost_usd asc nulls last" in query.lower()
    assert "avg_running_secs" not in query.lower()


@pytest.mark.anyio
async def test_top_agents_uses_cost_tiebreaker() -> None:
    conn = _FetchConn([_agent_score_row()])

    result = await agent_queries.get_top_agents.__wrapped__(conn, number_of_agents=10, page=2)

    assert len(result) == 1
    assert result[0].approval_review_status is None
    assert conn.args == (10, 10)
    assert conn.query is not None
    _assert_cost_tiebreak_query(conn.query)
    query = conn.query.lower()
    assert "ass.status::text <> 'cancelled'" in query
    assert "approval_review_status" in query
    assert "agent_final_review_statuses" in query
    assert "ass.approved is true" in query
    assert "approval_review_status is distinct from 'rejected'" in query
