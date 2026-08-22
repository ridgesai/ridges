from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import utils.database as _db


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_sets, competitions, agents RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_sets, competitions, agents RESTART IDENTITY CASCADE")


async def _insert_competition(conn, set_id: int, scoring_mode: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name)
        VALUES
            ($1, 'validator', 'problem-a'),
            ($1, 'validator', 'problem-b')
        """,
        set_id,
    )
    if scoring_mode is not None:
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


async def _insert_agent(conn, set_id: int, hotkey: str) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
        ) VALUES ($1, $2, $2, 1, 'finished', NOW(), '127.0.0.1', $3)
        """,
        agent_id,
        hotkey,
        set_id,
    )
    return agent_id


async def _insert_validator_evaluation(
    conn,
    *,
    agent_id: UUID,
    set_id: int,
    validator_hotkey: str,
    solved_a: bool,
    solved_b: bool,
) -> None:
    evaluation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at
        ) VALUES ($1, $2, $3, $4, 'validator', NOW())
        """,
        evaluation_id,
        agent_id,
        validator_hotkey,
        set_id,
    )
    await conn.executemany(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id, evaluation_id, problem_name, status, test_results, created_at,
            started_running_agent_at, finished_or_errored_at
        ) VALUES ($1, $2, $3, 'finished', $4::jsonb, NOW(), NOW(), NOW())
        """,
        [
            (uuid4(), evaluation_id, "problem-a", '[{"status":"pass"}]' if solved_a else '[{"status":"fail"}]'),
            (uuid4(), evaluation_id, "problem-b", '[{"status":"pass"}]' if solved_b else '[{"status":"fail"}]'),
        ],
    )


@pytest.mark.anyio
async def test_score_refresh_uses_policy_mode_not_numeric_set_order() -> None:
    cases = ((410, "consensus", 0.5), (420, "legacy", 0.75))
    async with _db.pool.acquire() as conn:
        for set_id, scoring_mode, expected_score in cases:
            await _insert_competition(conn, set_id, scoring_mode)
            agent_id = await _insert_agent(conn, set_id, f"{scoring_mode}-hotkey")
            await _insert_validator_evaluation(
                conn,
                agent_id=agent_id,
                set_id=set_id,
                validator_hotkey="validator-a",
                solved_a=True,
                solved_b=False,
            )
            await _insert_validator_evaluation(
                conn,
                agent_id=agent_id,
                set_id=set_id,
                validator_hotkey="validator-b",
                solved_a=True,
                solved_b=True,
            )

            await conn.execute("SELECT refresh_agent_scores_for_agent($1)", agent_id)
            score = await conn.fetchrow(
                "SELECT set_id, validator_count, final_score FROM agent_scores WHERE agent_id = $1",
                agent_id,
            )

            assert dict(score) == {
                "set_id": set_id,
                "validator_count": 2,
                "final_score": pytest.approx(expected_score),
            }


@pytest.mark.anyio
async def test_score_refresh_preserves_existing_row_without_initialized_policy() -> None:
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, 430, None)
        agent_id = await _insert_agent(conn, 430, "draft-policy-hotkey")
        await conn.execute(
            """
            INSERT INTO agent_scores (
                agent_id, miner_hotkey, name, version_num, created_at, status,
                set_id, approved, validator_count, final_score
            ) VALUES ($1, 'draft-policy-hotkey', 'draft-policy-hotkey', 1, NOW(),
                      'finished', 430, false, 2, 0.33)
            """,
            agent_id,
        )

        await conn.execute("SELECT refresh_agent_scores_for_agent($1)", agent_id)
        final_score = await conn.fetchval(
            "SELECT final_score FROM agent_scores WHERE agent_id = $1",
            agent_id,
        )

    assert final_score == pytest.approx(0.33)
