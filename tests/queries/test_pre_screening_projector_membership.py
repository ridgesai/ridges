from __future__ import annotations

from uuid import uuid4

import pytest

import utils.database as _db
from queries.pre_screening_judge import project_next_pre_screening_job_state


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agents, competitions RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE pre_screening_results, pre_screening_jobs, agents, competitions RESTART IDENTITY CASCADE"
        )


@pytest.mark.anyio
async def test_projector_only_projects_job_with_exact_agent_membership() -> None:
    valid_agent_id = uuid4()
    mismatched_agent_id = uuid4()
    legacy_agent_id = uuid4()
    valid_job_id = uuid4()
    mismatched_job_id = uuid4()
    legacy_job_id = uuid4()

    async with _db.pool.acquire() as conn:
        await conn.execute("INSERT INTO competitions (set_id) VALUES (1), (2)")
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
            ) VALUES
                ($1, 'valid-hotkey', 'valid', 1, 'pre_screening', NOW(), '127.0.0.1', 1),
                ($2, 'mismatch-hotkey', 'mismatch', 1, 'pre_screening', NOW(), '127.0.0.1', 2)
            """,
            valid_agent_id,
            mismatched_agent_id,
        )
        # Recreate a pre-migration agent whose membership has not been resolved.
        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER trg_agents_competition_membership")
            await conn.execute(
                """
                INSERT INTO agents (
                    agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
                ) VALUES ($1, 'legacy-hotkey', 'legacy', 1, 'pre_screening', NOW(), '127.0.0.1', NULL)
                """,
                legacy_agent_id,
            )
            await conn.execute("ALTER TABLE agents ENABLE TRIGGER trg_agents_competition_membership")

        # Recreate older terminal jobs that predate prospective job membership enforcement.
        async with conn.transaction():
            await conn.execute("ALTER TABLE pre_screening_jobs DISABLE TRIGGER ALL")
            await conn.execute(
                """
                INSERT INTO pre_screening_jobs (
                    job_id, agent_id, set_id, status, policy_version, created_at
                ) VALUES
                    ($1, $2, 1, 'failed', 'hardcoding-v1', NOW() - INTERVAL '2 hours'),
                    ($3, $4, NULL, 'failed', 'hardcoding-v1', NOW() - INTERVAL '1 hour')
                """,
                mismatched_job_id,
                mismatched_agent_id,
                legacy_job_id,
                legacy_agent_id,
            )
            await conn.execute("ALTER TABLE pre_screening_jobs ENABLE TRIGGER ALL")

        await conn.execute(
            """
            INSERT INTO pre_screening_jobs (
                job_id, agent_id, set_id, status, policy_version, created_at
            ) VALUES ($1, $2, 1, 'succeeded', 'hardcoding-v1', NOW())
            """,
            valid_job_id,
            valid_agent_id,
        )

    assert await project_next_pre_screening_job_state() is True
    assert await project_next_pre_screening_job_state() is False

    async with _db.pool.acquire() as conn:
        agents = {
            row["agent_id"]: row["status"]
            for row in await conn.fetch(
                "SELECT agent_id, status::text AS status FROM agents WHERE agent_id = ANY($1::uuid[])",
                [valid_agent_id, mismatched_agent_id, legacy_agent_id],
            )
        }
        jobs = {
            row["job_id"]: row["projected_at"]
            for row in await conn.fetch(
                "SELECT job_id, projected_at FROM pre_screening_jobs WHERE job_id = ANY($1::uuid[])",
                [valid_job_id, mismatched_job_id, legacy_job_id],
            )
        }

    assert agents == {
        valid_agent_id: "screening_1",
        mismatched_agent_id: "pre_screening",
        legacy_agent_id: "pre_screening",
    }
    assert jobs[valid_job_id] is not None
    assert jobs[mismatched_job_id] is None
    assert jobs[legacy_job_id] is None
