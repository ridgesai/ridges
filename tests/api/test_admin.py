from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import api.config as config
import utils.database as _db
from api.endpoints.admin import (
    ColdkeyBanRequest,  # reused body model
    put_disqualified_agent,
    require_coldkey_ban_admin,
)


@pytest.fixture
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualification_jobs, disqualified_agents, agents RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualification_jobs, disqualified_agents, agents RESTART IDENTITY CASCADE")


async def _insert_agent() -> UUID:
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, miner_coldkey, name, version_num,
                status, created_at, ip_address
            )
            VALUES ($1, $2, $3, 'test-agent', 0, 'evaluating', NOW(), '127.0.0.1')
            """,
            agent_id,
            f"hotkey-{agent_id}",
            f"coldkey-{agent_id}",
        )
    return agent_id


def _admin_creds() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=config.COLDKEY_BAN_ADMIN_API_KEY)


def test_auth_rejects_missing_credentials() -> None:
    with pytest.raises(HTTPException) as exc:
        require_coldkey_ban_admin(None)
    assert exc.value.status_code == 401


def test_auth_rejects_wrong_credentials() -> None:
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key")
    with pytest.raises(HTTPException) as exc:
        require_coldkey_ban_admin(creds)
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_disqualify_existing_agent_succeeds(clean_tables) -> None:
    agent_id = await _insert_agent()

    result = await put_disqualified_agent(agent_id, ColdkeyBanRequest(reason="cheating"))

    assert result.agent_id == agent_id
    assert result.reason == "cheating"

    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM disqualified_agents WHERE agent_id = $1", agent_id)
    assert count == 1


@pytest.mark.anyio
async def test_disqualify_unknown_agent_returns_404(clean_tables) -> None:
    with pytest.raises(HTTPException) as exc:
        await put_disqualified_agent(uuid4(), ColdkeyBanRequest(reason="cheating"))
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_disqualify_enqueues_reapproval_job(clean_tables) -> None:
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        # seed competition + agent with a set_id
        await conn.execute("INSERT INTO competitions (set_id) VALUES (71) ON CONFLICT DO NOTHING")
        await conn.execute(
            """
            INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num,
                                status, created_at, ip_address, set_id)
            VALUES ($1, $2, $3, 'test-agent', 0, 'evaluating', NOW(), '127.0.0.1', 71)
            """,
            agent_id,
            f"hotkey-{agent_id}",
            f"coldkey-{agent_id}",
        )

    result = await put_disqualified_agent(agent_id, ColdkeyBanRequest(reason="cheating"))
    assert result.agent_id == agent_id

    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT agent_id FROM disqualification_jobs WHERE agent_id = $1", agent_id)
    assert row is not None
