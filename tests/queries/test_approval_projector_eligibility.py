from __future__ import annotations

from uuid import uuid4

import pytest

import utils.database as _db
from queries.approval import project_next_approval_job_state


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agent_approval_states, approval_jobs, approved_agents, agents, competitions "
            "RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agent_approval_states, approval_jobs, approved_agents, agents, competitions "
            "RESTART IDENTITY CASCADE"
        )


@pytest.mark.anyio
async def test_projector_skips_ineligible_legacy_jobs_without_blocking_valid_work() -> None:
    missing_policy_agent_id = uuid4()
    mismatched_agent_id = uuid4()
    valid_agent_id = uuid4()
    missing_policy_job_id = uuid4()
    mismatched_job_id = uuid4()
    valid_job_id = uuid4()

    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO competitions (set_id, start_date)
            VALUES (1, NOW())
            """
        )
        await conn.execute(
            """
            INSERT INTO competitions (
                set_id, start_date, scoring_mode, screener_1_threshold, screener_2_threshold,
                prune_threshold, required_validator_count, pre_screening_enabled,
                auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
                incentive_performance_threshold, incentive_cost_threshold,
                incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
            ) VALUES
                (2, NOW(), 'consensus', 0.4, 0.4, 0.4, 3, true, true,
                 'hardcoding-v1', false, 0.03, 0.06, 336, 12),
                (3, NOW(), 'consensus', 0.4, 0.4, 0.4, 3, true, true,
                 'hardcoding-v1', false, 0.03, 0.06, 336, 12)
            """
        )
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
            ) VALUES
                ($1, 'missing-policy-hotkey', 'missing-policy', 0, 'finished', NOW(), '127.0.0.1', 1),
                ($2, 'mismatched-hotkey', 'mismatched', 0, 'finished', NOW(), '127.0.0.1', 3),
                ($3, 'valid-hotkey', 'valid', 0, 'finished', NOW(), '127.0.0.1', 2)
            """,
            missing_policy_agent_id,
            mismatched_agent_id,
            valid_agent_id,
        )
        await conn.execute(
            """
            INSERT INTO approval_jobs (
                job_id, agent_id, set_id, status, policy_version, input_snapshot,
                aggregate_verdict, aggregate_score, aggregate_confidence,
                aggregate_summary, decision_source, created_at
            ) VALUES (
                $1, $2, 1, 'completed', 'hardcoding-v1', '{}'::jsonb,
                'approved', 0.8, 0.7, 'missing policy', 'auto_judge', NOW() - INTERVAL '3 hours'
            )
            """,
            missing_policy_job_id,
            missing_policy_agent_id,
        )

        # Recreate a terminal row that predates prospective same-set enforcement.
        async with conn.transaction():
            await conn.execute("ALTER TABLE approval_jobs DISABLE TRIGGER ALL")
            await conn.execute(
                """
                INSERT INTO approval_jobs (
                    job_id, agent_id, set_id, status, policy_version, input_snapshot,
                    aggregate_verdict, aggregate_score, aggregate_confidence,
                    aggregate_summary, decision_source, created_at
                ) VALUES (
                    $1, $2, 2, 'completed', 'hardcoding-v1', '{}'::jsonb,
                    'approved', 0.8, 0.7, 'mismatched membership', 'auto_judge',
                    NOW() - INTERVAL '2 hours'
                )
                """,
                mismatched_job_id,
                mismatched_agent_id,
            )
            await conn.execute("ALTER TABLE approval_jobs ENABLE TRIGGER ALL")

        await conn.execute(
            """
            INSERT INTO approval_jobs (
                job_id, agent_id, set_id, status, policy_version, input_snapshot,
                aggregate_verdict, aggregate_score, aggregate_confidence,
                aggregate_summary, decision_source, created_at
            ) VALUES (
                $1, $2, 2, 'completed', 'hardcoding-v1', '{}'::jsonb,
                'approved', 0.8, 0.7, 'valid', 'auto_judge', NOW() - INTERVAL '1 hour'
            )
            """,
            valid_job_id,
            valid_agent_id,
        )

    assert await project_next_approval_job_state() is True
    assert await project_next_approval_job_state() is False

    async with _db.pool.acquire() as conn:
        jobs = {
            row["job_id"]: row["projected_at"]
            for row in await conn.fetch(
                "SELECT job_id, projected_at FROM approval_jobs WHERE job_id = ANY($1::uuid[])",
                [missing_policy_job_id, mismatched_job_id, valid_job_id],
            )
        }
        states = await conn.fetch("SELECT agent_id, set_id, latest_job_id FROM agent_approval_states ORDER BY agent_id")

    assert jobs[missing_policy_job_id] is None
    assert jobs[mismatched_job_id] is None
    assert jobs[valid_job_id] is not None
    assert [dict(row) for row in states] == [{"agent_id": valid_agent_id, "set_id": 2, "latest_job_id": valid_job_id}]
