from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import api.config as config
import utils.database as _db
from models.agent import AgentCreate
from queries.agent import create_agent
from queries.competition import initialize_current_competition_policy


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agent_openrouter_secrets, agents, competitions "
            "RESTART IDENTITY CASCADE"
        )
        await conn.execute("INSERT INTO competitions (set_id, start_date) VALUES (1, NOW())")
    await initialize_current_competition_policy()
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agent_openrouter_secrets, agents, competitions "
            "RESTART IDENTITY CASCADE"
        )


async def _create(monkeypatch, *, policy_version: str) -> UUID:
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE competitions
            SET pre_screening_enabled = true, hardcoding_policy_version = $1
            WHERE set_id = 1
            """,
            policy_version,
        )
    agent = AgentCreate(
        miner_hotkey="policy-hotkey",
        name="policy-agent",
        version_num=0,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash=f"block-{policy_version}",
        payment_extrinsic_index="0",
    )
    return await create_agent(
        agent,
        "print('hi')\n",
        source_sha256=f"sha-{policy_version}",
        runtime_openrouter_api_key_ciphertext=b"runtime",
        management_openrouter_api_key_ciphertext=b"management",
        openrouter_workspace_id="workspace",
        openrouter_api_key_label="label",
        openrouter_api_key_creator_user_id="creator",
        openrouter_validated_at=datetime.now(timezone.utc),
    )


@pytest.mark.anyio
async def test_create_agent_stamps_policy_version_on_pre_screening_job(monkeypatch) -> None:
    agent_id = await _create(monkeypatch, policy_version="hardcoding-linting-v1")

    async with _db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT policy_version, status FROM pre_screening_jobs WHERE agent_id = $1",
            agent_id,
        )
    assert [(row["policy_version"], row["status"]) for row in rows] == [("hardcoding-linting-v1", "pending")]


@pytest.mark.anyio
async def test_create_agent_ignores_global_policy_version(monkeypatch) -> None:
    monkeypatch.setattr(config, "HARDCODING_POLICY_VERSION", "global-opposite")
    agent_id = await _create(monkeypatch, policy_version="stored-policy-v2")

    async with _db.pool.acquire() as conn:
        policy_version = await conn.fetchval(
            "SELECT policy_version FROM pre_screening_jobs WHERE agent_id = $1",
            agent_id,
        )
    assert policy_version == "stored-policy-v2"
