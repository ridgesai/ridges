"""Tests for GET /retrieval/agents-by-coldkey."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.database as _db
from api.endpoints import retrieval as retrieval_module
from models.agent import PublicAgent
from utils.ttl import clear_all_ttl_caches

pytestmark = pytest.mark.anyio

COLDKEY = "5FConsoleColdkey1"
OTHER_COLDKEY = "5FConsoleColdkey2"
HOTKEY_A = "5FConsoleHotkeyA1"
HOTKEY_B = "5FConsoleHotkeyB1"

BASE_TIME = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    clear_all_ttl_caches()
    yield
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE agents RESTART IDENTITY CASCADE")


async def _insert_agent(
    hotkey: str,
    coldkey: str | None,
    name: str,
    version_num: int = 0,
    created_at: datetime | None = None,
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num, status, created_at, ip_address)
            VALUES ($1, $2, $3, $4, $5, 'screening_1', $6, '127.0.0.1')
            """,
            agent_id,
            hotkey,
            coldkey,
            name,
            version_num,
            created_at or (BASE_TIME + timedelta(minutes=version_num)),
        )
    return agent_id


async def test_groups_agents_by_hotkey():
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 0)
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)
    await _insert_agent(HOTKEY_B, COLDKEY, "agent-b", 0)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert list(result.keys()) == [HOTKEY_A, HOTKEY_B]  # hotkeys sorted
    assert [a.version_num for a in result[HOTKEY_A]] == [1, 0]  # newest first
    assert all(a.miner_hotkey == HOTKEY_A for a in result[HOTKEY_A])
    assert len(result[HOTKEY_B]) == 1


async def test_null_coldkey_rows_are_excluded():
    await _insert_agent(HOTKEY_A, None, "dev-agent")

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert result == {}


async def test_unknown_coldkey_returns_empty_dict():
    result = await retrieval_module.agents_by_coldkey(miner_coldkey="5FNobody")
    assert result == {}


async def test_legacy_null_rows_on_owned_hotkey_are_excluded():
    # Agents uploaded before miner_coldkey existed (2026-07-10) carry NULL
    await _insert_agent(HOTKEY_A, None, "agent-a", 0, created_at=BASE_TIME - timedelta(days=60))
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert [a.version_num for a in result[HOTKEY_A]] == [1]


async def test_foreign_coldkey_rows_on_shared_hotkey_are_excluded():
    await _insert_agent(HOTKEY_A, OTHER_COLDKEY, "agent-a", 0)
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert [a.version_num for a in result[HOTKEY_A]] == [1]


async def test_payload_is_public_agent_shape():
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a")

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    agent = result[HOTKEY_A][0]
    payload = agent.model_dump()
    assert "agent_id" in payload and "status" in payload
    assert "ip_address" not in payload


async def test_route_serializes_and_requires_miner_coldkey(monkeypatch):
    clear_all_ttl_caches()
    public_agent = PublicAgent(
        agent_id=uuid.uuid4(),
        miner_hotkey=HOTKEY_A,
        name="agent-a",
        version_num=0,
        status="screening_1",
        created_at=BASE_TIME,
    )
    monkeypatch.setattr(
        retrieval_module,
        "get_all_public_agents_by_miner_coldkey",
        AsyncMock(return_value=[public_agent]),
    )
    app = FastAPI()
    app.include_router(retrieval_module.router, prefix="/retrieval")
    client = TestClient(app)

    assert client.get("/retrieval/agents-by-coldkey").status_code == 422  # param required

    response = client.get("/retrieval/agents-by-coldkey", params={"miner_coldkey": COLDKEY})
    assert response.status_code == 200
    body = response.json()
    assert list(body.keys()) == [HOTKEY_A]
    assert body[HOTKEY_A][0]["name"] == "agent-a"
    assert "ip_address" not in body[HOTKEY_A][0]
    clear_all_ttl_caches()
