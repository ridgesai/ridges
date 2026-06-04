from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from models.agent import AgentStatus
from models.queue import QueueStage
from queries import agent as agent_queries


def _agent_row(*, status: AgentStatus = AgentStatus.pre_screening):
    return {
        "agent_id": uuid4(),
        "miner_hotkey": "miner-hotkey",
        "name": "Agent",
        "version_num": 0,
        "status": status.value,
        "created_at": datetime.now(timezone.utc),
        "ip_address": "127.0.0.1",
    }


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.query: str | None = None
        self.args: tuple = ()

    async def fetch(self, query: str, *args):
        self.query = query
        self.args = args
        return self.rows


@pytest.mark.anyio
async def test_get_agents_in_pre_screening_queue_uses_stage_view() -> None:
    conn = _FakeConn([_agent_row()])

    result = await agent_queries.get_agents_in_queue.__wrapped__(conn, QueueStage.pre_screening)

    assert [agent.status for agent in result] == [AgentStatus.pre_screening]
    assert conn.args == ()
    assert conn.query is not None
    assert "join pre_screening_queue q" in conn.query
    assert "order by a.created_at asc" in conn.query
