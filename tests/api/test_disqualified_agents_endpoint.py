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
        await conn.execute("TRUNCATE disqualified_agents, agents RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualified_agents, agents RESTART IDENTITY CASCADE")


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
