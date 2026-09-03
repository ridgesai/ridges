from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic.config import Config

import utils.database as _db
from alembic import command

BASE_REVISION = "6f59f4e0c487"
HEAD_REVISION = "07af81b81a3e"
REPO_ROOT = Path(__file__).resolve().parents[2]

MEMBERSHIP_FKS = {
    "fk_evaluations_agent_competition",
    "fk_pre_screening_jobs_agent_competition",
    "fk_approval_jobs_agent_competition",
    "fk_agent_approval_states_agent_competition",
    "fk_approved_agents_agent_competition",
    "fk_agent_scores_agent_competition",
}
CROSS_REFERENCE_FKS = {
    "fk_approved_agents_baseline_competition",
    "fk_agent_approval_states_latest_job",
}


async def _upgrade(revision: str) -> None:
    await asyncio.to_thread(command.upgrade, Config(REPO_ROOT / "alembic.ini"), revision)


async def _downgrade(revision: str) -> None:
    await asyncio.to_thread(command.downgrade, Config(REPO_ROOT / "alembic.ini"), revision)


async def _set_policy(conn, set_id: int, *, scoring_mode: str = "consensus") -> None:
    await conn.execute(
        """
        UPDATE competitions
        SET
            scoring_mode = $2,
            screener_1_threshold = 0.4,
            screener_2_threshold = 0.4,
            prune_threshold = 0.4,
            required_validator_count = 3,
            pre_screening_enabled = true,
            auto_approval_enabled = true,
            hardcoding_policy_version = 'hardcoding-v1',
            incentive_enabled = false,
            incentive_performance_threshold = 0.03,
            incentive_cost_threshold = 0.06,
            incentive_reward_half_life_hours = 336,
            incentive_time_multiplier_scale_hours = 12
        WHERE set_id = $1
        """,
        set_id,
        scoring_mode,
    )


@pytest.mark.anyio
async def test_membership_migration_preserves_legacy_rows_and_restores_exact_functions(postgres_db) -> None:
    bound_agent_id = uuid4()
    legacy_agent_id = uuid4()
    other_set_agent_id = uuid4()
    legacy_cross_set_job_id = uuid4()
    try:
        async with _db.pool.acquire() as conn:
            await conn.execute("TRUNCATE evaluation_sets, competitions, agents RESTART IDENTITY CASCADE")

        await _downgrade(BASE_REVISION)

        async with _db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO evaluation_sets (set_id, set_group, problem_name)
                VALUES
                    (100, 'validator', 'problem-a'),
                    (101, 'validator', 'problem-b')
                """
            )
            await _set_policy(conn, 100)
            await _set_policy(conn, 101)
            await conn.execute(
                """
                INSERT INTO agents (
                    agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
                ) VALUES
                    ($1, 'bound', 'bound', 1, 'finished', NOW(), '127.0.0.1', 100),
                    ($2, 'legacy', 'legacy', 1, 'finished', NOW(), '127.0.0.1', NULL),
                    ($3, 'other-set', 'other-set', 1, 'finished', NOW(), '127.0.0.1', 101)
                """,
                bound_agent_id,
                legacy_agent_id,
                other_set_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO pre_screening_jobs (agent_id, policy_version, status)
                VALUES
                    ($1, 'hardcoding-v1', 'succeeded'),
                    ($2, 'hardcoding-v1', 'succeeded')
                """,
                bound_agent_id,
                legacy_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO evaluations (
                    evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at
                ) VALUES ($1, $2, 'validator-old', 101, 'validator', NOW())
                """,
                uuid4(),
                bound_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO agent_scores (
                    agent_id, miner_hotkey, name, version_num, created_at, status,
                    set_id, approved, validator_count, final_score
                ) VALUES ($1, 'legacy', 'legacy', 1, NOW(), 'finished', 100, false, 2, 0.25)
                """,
                legacy_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO approved_agents (agent_id, set_id, baseline_agent_id)
                VALUES ($1, 100, $2)
                """,
                bound_agent_id,
                other_set_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO approval_jobs (
                    job_id, agent_id, set_id, policy_version, input_snapshot
                ) VALUES ($1, $2, 100, 'hardcoding-v1', '{}'::jsonb)
                """,
                legacy_cross_set_job_id,
                bound_agent_id,
            )
            await conn.execute(
                """
                INSERT INTO agent_approval_states (
                    agent_id, set_id, latest_job_id, processing_status
                ) VALUES ($1, 101, $2, 'pending')
                """,
                other_set_agent_id,
                legacy_cross_set_job_id,
            )
            old_refresh = await conn.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure('refresh_agent_scores_for_agent(uuid)'))"
            )
            old_populate = await conn.fetchval("SELECT pg_get_functiondef(to_regprocedure('populate_agent_scores()'))")
            old_wrapper = await conn.fetchval("SELECT pg_get_functiondef(to_regprocedure('refresh_agent_scores()'))")

        await _upgrade(HEAD_REVISION)

        async with _db.pool.acquire() as conn:
            jobs = await conn.fetch("SELECT agent_id, set_id FROM pre_screening_jobs ORDER BY agent_id")
            jobs_by_agent = {row["agent_id"]: row["set_id"] for row in jobs}
            assert jobs_by_agent == {bound_agent_id: 100, legacy_agent_id: None}
            assert await conn.fetchval(
                "SELECT final_score FROM agent_scores WHERE agent_id = $1",
                legacy_agent_id,
            ) == pytest.approx(0.25)
            await conn.execute(
                "UPDATE agents SET status = 'cancelled' WHERE agent_id = $1",
                legacy_agent_id,
            )
            await conn.execute(
                "UPDATE pre_screening_jobs SET status = 'failed' WHERE agent_id = $1",
                legacy_agent_id,
            )
            assert (
                await conn.fetchval(
                    "SELECT status::text FROM agents WHERE agent_id = $1",
                    legacy_agent_id,
                )
                == "cancelled"
            )
            assert (
                await conn.fetchval(
                    "SELECT status FROM pre_screening_jobs WHERE agent_id = $1",
                    legacy_agent_id,
                )
                == "failed"
            )
            assert (
                await conn.fetchval(
                    "SELECT set_id FROM evaluations WHERE agent_id = $1",
                    bound_agent_id,
                )
                == 101
            )
            assert (
                await conn.fetchval(
                    "SELECT baseline_agent_id FROM approved_agents WHERE agent_id = $1 AND set_id = 100",
                    bound_agent_id,
                )
                == other_set_agent_id
            )
            assert (
                await conn.fetchval(
                    "SELECT latest_job_id FROM agent_approval_states WHERE agent_id = $1 AND set_id = 101",
                    other_set_agent_id,
                )
                == legacy_cross_set_job_id
            )
            await conn.execute(
                "UPDATE approved_agents SET performance_delta = 0.1 WHERE agent_id = $1 AND set_id = 100",
                bound_agent_id,
            )
            await conn.execute(
                """
                UPDATE agent_approval_states
                SET processing_status = 'running'
                WHERE agent_id = $1 AND set_id = 101
                """,
                other_set_agent_id,
            )

            constraints = {
                row["conname"]: row
                for row in await conn.fetch(
                    """
                    SELECT conname, convalidated, condeferrable, condeferred
                    FROM pg_constraint
                    WHERE conname = ANY($1::text[])
                    """,
                    [*MEMBERSHIP_FKS, *CROSS_REFERENCE_FKS, "fk_evaluation_sets_competition"],
                )
            }
            assert set(constraints) == {
                *MEMBERSHIP_FKS,
                *CROSS_REFERENCE_FKS,
                "fk_evaluation_sets_competition",
            }
            assert all(not constraints[name]["convalidated"] for name in constraints)
            assert constraints["fk_evaluation_sets_competition"]["condeferrable"] is True
            assert constraints["fk_evaluation_sets_competition"]["condeferred"] is True
            assert await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_approval_jobs_job_id_agent_id_set_id'
                      AND contype = 'u'
                )
                """
            )
            assert await conn.fetchval(
                "SELECT performance_delta FROM approved_agents WHERE agent_id = $1 AND set_id = 100",
                bound_agent_id,
            ) == pytest.approx(0.1)
            assert (
                await conn.fetchval(
                    "SELECT processing_status FROM agent_approval_states WHERE agent_id = $1 AND set_id = 101",
                    other_set_agent_id,
                )
                == "running"
            )

            new_agent_id = uuid4()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, miner_hotkey, name, version_num, status, created_at, ip_address
                    ) VALUES ($1, 'new-null', 'new-null', 1, 'finished', NOW(), '127.0.0.1')
                    """,
                    new_agent_id,
                )

            await conn.execute("UPDATE agents SET set_id = 100 WHERE agent_id = $1", legacy_agent_id)
            await conn.execute(
                "UPDATE pre_screening_jobs SET set_id = 100 WHERE agent_id = $1",
                legacy_agent_id,
            )
            assert (
                await conn.fetchval(
                    "SELECT set_id FROM pre_screening_jobs WHERE agent_id = $1",
                    legacy_agent_id,
                )
                == 100
            )
            for new_set_id in (None, 101):
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(
                        "UPDATE agents SET set_id = $2 WHERE agent_id = $1",
                        legacy_agent_id,
                        new_set_id,
                    )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE pre_screening_jobs SET set_id = NULL WHERE agent_id = $1",
                    legacy_agent_id,
                )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    "UPDATE pre_screening_jobs SET set_id = 101 WHERE agent_id = $1",
                    legacy_agent_id,
                )

            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    """
                    INSERT INTO evaluations (
                        evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at
                    ) VALUES ($1, $2, 'validator-new', 101, 'validator', NOW())
                    """,
                    uuid4(),
                    legacy_agent_id,
                )

            invalid_membership_inserts = [
                (
                    """
                    INSERT INTO approval_jobs (
                        agent_id, set_id, policy_version, input_snapshot
                    ) VALUES ($1, 101, 'hardcoding-v1', '{}'::jsonb)
                    """,
                    legacy_agent_id,
                ),
                (
                    """
                    INSERT INTO agent_approval_states (agent_id, set_id)
                    VALUES ($1, 101)
                    """,
                    legacy_agent_id,
                ),
                (
                    "INSERT INTO approved_agents (agent_id, set_id) VALUES ($1, 101)",
                    legacy_agent_id,
                ),
                (
                    """
                    INSERT INTO agent_scores (
                        agent_id, miner_hotkey, name, version_num, created_at, status,
                        set_id, approved, validator_count, final_score
                    ) VALUES ($1, 'legacy', 'legacy', 1, NOW(), 'cancelled', 101, false, 2, 0.2)
                    """,
                    legacy_agent_id,
                ),
            ]
            for statement, agent_id in invalid_membership_inserts:
                with pytest.raises(asyncpg.ForeignKeyViolationError):
                    await conn.execute(statement, agent_id)

            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    """
                    INSERT INTO approved_agents (agent_id, set_id, baseline_agent_id)
                    VALUES ($1, 100, $2)
                    """,
                    legacy_agent_id,
                    other_set_agent_id,
                )
            await conn.execute(
                """
                INSERT INTO approved_agents (agent_id, set_id, baseline_agent_id)
                VALUES ($1, 100, $2)
                """,
                legacy_agent_id,
                bound_agent_id,
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    """
                    UPDATE approved_agents
                    SET baseline_agent_id = $2
                    WHERE agent_id = $1 AND set_id = 100
                    """,
                    legacy_agent_id,
                    other_set_agent_id,
                )

            matching_job_id = uuid4()
            other_set_job_id = uuid4()
            await conn.execute(
                """
                INSERT INTO approval_jobs (
                    job_id, agent_id, set_id, policy_version, input_snapshot
                ) VALUES
                    ($1, $2, 100, 'hardcoding-v1', '{}'::jsonb),
                    ($3, $4, 101, 'hardcoding-v1', '{}'::jsonb)
                """,
                matching_job_id,
                legacy_agent_id,
                other_set_job_id,
                other_set_agent_id,
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    """
                    INSERT INTO agent_approval_states (
                        agent_id, set_id, latest_job_id, processing_status
                    ) VALUES ($1, 100, $2, 'pending')
                    """,
                    legacy_agent_id,
                    other_set_job_id,
                )
            await conn.execute(
                """
                INSERT INTO agent_approval_states (
                    agent_id, set_id, latest_job_id, processing_status
                ) VALUES ($1, 100, $2, 'pending')
                """,
                legacy_agent_id,
                matching_job_id,
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    """
                    UPDATE agent_approval_states
                    SET latest_job_id = $2
                    WHERE agent_id = $1 AND set_id = 100
                    """,
                    legacy_agent_id,
                    other_set_job_id,
                )

            await conn.execute("DELETE FROM approval_jobs WHERE job_id = $1", legacy_cross_set_job_id)
            deleted_job_reference = await conn.fetchrow(
                "SELECT latest_job_id, set_id FROM agent_approval_states WHERE agent_id = $1 AND set_id = 101",
                other_set_agent_id,
            )
            assert dict(deleted_job_reference) == {"latest_job_id": None, "set_id": 101}
            await conn.execute("DELETE FROM agents WHERE agent_id = $1", other_set_agent_id)
            deleted_baseline_reference = await conn.fetchrow(
                "SELECT baseline_agent_id, set_id FROM approved_agents WHERE agent_id = $1 AND set_id = 100",
                bound_agent_id,
            )
            assert dict(deleted_baseline_reference) == {"baseline_agent_id": None, "set_id": 100}

            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO pre_screening_jobs (agent_id, set_id, policy_version)
                    VALUES ($1, NULL, 'hardcoding-v1')
                    """,
                    legacy_agent_id,
                )

            await conn.execute(
                """
                INSERT INTO evaluation_sets (set_id, set_group, problem_name)
                VALUES (200, 'validator', 'draft-problem')
                """
            )
            assert await conn.fetchval("SELECT start_date FROM competitions WHERE set_id = 200") is None

            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with conn.transaction():
                    await conn.execute("ALTER TABLE evaluation_sets DISABLE TRIGGER trg_evaluation_sets_new_set_id")
                    await conn.execute(
                        """
                        INSERT INTO evaluation_sets (set_id, set_group, problem_name)
                        VALUES (201, 'validator', 'orphan-problem')
                        """
                    )

        await _downgrade(BASE_REVISION)

        async with _db.pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT pg_get_functiondef(to_regprocedure('refresh_agent_scores_for_agent(uuid)'))"
                )
                == old_refresh
            )
            assert (
                await conn.fetchval("SELECT pg_get_functiondef(to_regprocedure('populate_agent_scores()'))")
                == old_populate
            )
            assert (
                await conn.fetchval("SELECT pg_get_functiondef(to_regprocedure('refresh_agent_scores()'))")
                == old_wrapper
            )
            await conn.execute(
                """
                INSERT INTO evaluation_sets (set_id, set_group, problem_name)
                VALUES (300, 'validator', 'started-problem')
                """
            )
            assert await conn.fetchval("SELECT start_date FROM competitions WHERE set_id = 300") is not None
    finally:
        async with _db.pool.acquire() as conn:
            await conn.execute("TRUNCATE evaluation_sets, competitions, agents RESTART IDENTITY CASCADE")
        await _upgrade(HEAD_REVISION)
