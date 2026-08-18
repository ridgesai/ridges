from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import utils.database as _db
from models.agent import AgentCreate, AgentStatus
from queries.agent import create_agent


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agent_openrouter_secrets, agents "
            "RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agent_openrouter_secrets, agents "
            "RESTART IDENTITY CASCADE"
        )


async def _create(monkeypatch, *, policy_version: str) -> UUID:
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    agent = AgentCreate(
        miner_hotkey="policy-hotkey",
        name="policy-agent",
        version_num=0,
        status=AgentStatus.pre_screening,
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
        create_pre_screening_job=True,
        pre_screening_policy_version=policy_version,
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
async def test_create_agent_requires_an_explicit_policy_version(monkeypatch) -> None:
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    agent = AgentCreate(
        miner_hotkey="policy-hotkey",
        name="policy-agent",
        version_num=0,
        status=AgentStatus.pre_screening,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash="block-x",
        payment_extrinsic_index="0",
    )
    with pytest.raises(TypeError):
        await create_agent(
            agent,
            "print('hi')\n",
            source_sha256="sha-x",
            runtime_openrouter_api_key_ciphertext=b"runtime",
            management_openrouter_api_key_ciphertext=b"management",
            openrouter_workspace_id="workspace",
            openrouter_api_key_label="label",
            openrouter_api_key_creator_user_id="creator",
            openrouter_validated_at=datetime.now(timezone.utc),
        )
