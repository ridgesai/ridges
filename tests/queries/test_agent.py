"""Integration tests for get_next_agent_id_awaiting_evaluation_for_validator_hotkey."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import utils.database as _db
from queries.agent import (
    get_agent_score_and_set_id,
    get_next_agent_id_awaiting_evaluation_for_validator_hotkey,
)

HOTKEY = "validator-hotkey-1"
OTHER_HOTKEY = "validator-hotkey-2"
SET_CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)

# anyio_backend is defined in tests/conftest.py and inherited automatically.

_CLEAN_TABLES_SQL = (
    "TRUNCATE evaluation_runs, evaluations, evaluation_sets, benchmark_agent_ids, banned_coldkeys, "
    "banned_hotkeys, agent_scores, agents, competitions RESTART IDENTITY CASCADE"
)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(_CLEAN_TABLES_SQL)
        await _insert_competition(conn, set_id=1, required_validator_count=1)
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(_CLEAN_TABLES_SQL)


async def _insert_agent(
    *,
    status: str = "evaluating",
    created_at: datetime | None = None,
    miner_coldkey: str | None = None,
    miner_hotkey: str = "test-hotkey",
    set_id: int = 1,
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, miner_coldkey, name, version_num,
                status, created_at, ip_address, set_id
            )
            VALUES ($1, $2, $3, $4, $5, $6::agentstatus, $7, $8, $9)
            """,
            agent_id,
            miner_hotkey,
            miner_coldkey,
            "test-agent",
            1,
            status,
            created_at or datetime.now(timezone.utc),
            "127.0.0.1",
            set_id,
        )
    return agent_id


async def _insert_eval_set(set_id: int) -> None:
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1, $2, $3, $4)",
            set_id,
            "validator",
            "problem-a",
            SET_CREATED,
        )


async def _insert_competition(conn, *, set_id: int, required_validator_count: int) -> None:
    await conn.execute(
        """
        INSERT INTO competitions (
            set_id, start_date, scoring_mode, screener_1_threshold, screener_2_threshold,
            prune_threshold, required_validator_count, pre_screening_enabled,
            auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
            incentive_performance_threshold, incentive_cost_threshold,
            incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
        ) VALUES ($1, NOW(), 'consensus', 0.4, 0.4, 0.4, $2, true, true,
                  'hardcoding-v1', false, 0.03, 0.06, 336, 12)
        ON CONFLICT (set_id) DO UPDATE
        SET start_date = EXCLUDED.start_date,
            required_validator_count = EXCLUDED.required_validator_count,
            scoring_mode = EXCLUDED.scoring_mode,
            screener_1_threshold = EXCLUDED.screener_1_threshold,
            screener_2_threshold = EXCLUDED.screener_2_threshold,
            prune_threshold = EXCLUDED.prune_threshold,
            pre_screening_enabled = EXCLUDED.pre_screening_enabled,
            auto_approval_enabled = EXCLUDED.auto_approval_enabled,
            hardcoding_policy_version = EXCLUDED.hardcoding_policy_version,
            incentive_enabled = EXCLUDED.incentive_enabled,
            incentive_performance_threshold = EXCLUDED.incentive_performance_threshold,
            incentive_cost_threshold = EXCLUDED.incentive_cost_threshold,
            incentive_reward_half_life_hours = EXCLUDED.incentive_reward_half_life_hours,
            incentive_time_multiplier_scale_hours = EXCLUDED.incentive_time_multiplier_scale_hours
        """,
        set_id,
        required_validator_count,
    )


async def _insert_agent_score(*, agent_id: uuid.UUID, set_id: int, final_score: float) -> None:
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO agent_scores
                   (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, approved,
                    validator_count, final_score)
               VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9)""",
            agent_id,
            f"hotkey-{agent_id}",
            f"agent-{agent_id}",
            1,
            "finished",
            set_id,
            False,
            1,
            final_score,
        )


async def _insert_evaluation(
    agent_id: uuid.UUID,
    *,
    group: str,
    validator_hotkey: str = HOTKEY,
    set_id: int = 1,
) -> uuid.UUID:
    evaluation_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group)
            VALUES ($1, $2, $3, $4, $5::evaluationsetgroup)
            """,
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            group,
        )
    return evaluation_id


async def _insert_run(
    evaluation_id: uuid.UUID,
    *,
    status: str,
    error_code: int | None = None,
    verifier_reward: float | None = None,
    test_results: list | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_runs (
                evaluation_run_id, evaluation_id, problem_name, status,
                error_code, verifier_reward, test_results, created_at
            )
            VALUES ($1, $2, $3, $4::evaluationrunstatus, $5, $6, $7::jsonb, $8)
            """,
            run_id,
            evaluation_id,
            "test-problem",
            status,
            error_code,
            verifier_reward,
            json.dumps(test_results) if test_results is not None else None,
            datetime.now(timezone.utc),
        )
    return run_id


@pytest.mark.anyio
async def test_returns_none_when_no_candidates():
    """No evaluating agents → None."""
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_returns_agent_with_no_evaluations():
    """Single evaluating agent with zero evaluations → returned."""
    agent_id = await _insert_agent()
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "queue_view"),
    [
        ("pre_screening", "pre_screening_queue"),
        ("screening_1", "screener_1_queue"),
        ("screening_2", "screener_2_queue"),
    ],
)
async def test_stage_queues_exclude_banned_coldkeys_but_keep_null_coldkeys(status: str, queue_view: str):
    banned_agent = await _insert_agent(status=status, miner_coldkey="banned-coldkey", miner_hotkey="banned-hotkey")
    null_coldkey_agent = await _insert_agent(status=status, miner_coldkey=None, miner_hotkey="owner-hotkey")
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ('banned-coldkey', 'test ban')"
        )
        queued = await conn.fetch(f"SELECT agent_id FROM {queue_view}")

    assert {row["agent_id"] for row in queued} == {null_coldkey_agent}
    assert banned_agent not in {row["agent_id"] for row in queued}


@pytest.mark.anyio
async def test_validator_queue_excludes_banned_coldkey():
    banned_agent = await _insert_agent(miner_coldkey="banned-coldkey", miner_hotkey="banned-hotkey")
    eligible_agent = await _insert_agent(miner_coldkey="eligible-coldkey", miner_hotkey="eligible-hotkey")
    for agent_id in (banned_agent, eligible_agent):
        evaluation_id = await _insert_evaluation(agent_id, group="screener_2")
        await _insert_run(evaluation_id, status="finished", verifier_reward=1.0)

    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ('banned-coldkey', 'test ban')"
        )
        queued = await conn.fetch("SELECT agent_id FROM validator_queue")

    assert {row["agent_id"] for row in queued} == {eligible_agent}


@pytest.mark.anyio
async def test_legacy_hotkey_ban_no_longer_removes_agent_from_validator_queue():
    agent_id = await _insert_agent(miner_coldkey="eligible-coldkey", miner_hotkey="legacy-banned-hotkey")
    evaluation_id = await _insert_evaluation(agent_id, group="screener_2")
    await _insert_run(evaluation_id, status="finished", verifier_reward=1.0)

    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) VALUES ('legacy-banned-hotkey', 'legacy ban')"
        )

    assert await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY) == agent_id


@pytest.mark.anyio
async def test_skips_benchmark_agent():
    """Agent in benchmark_agent_ids is excluded even when evaluating."""
    agent_id = await _insert_agent()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, $2)",
            agent_id,
            "benchmark",
        )
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_already_evaluated_by_this_validator():
    """Agent with a finished validator-group eval by HOTKEY → already_evaluated=true → None."""
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=HOTKEY)
    await _insert_run(eval_id, status="finished")
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_at_running_eval_limit():
    """Agent already has NUM_EVALS_PER_AGENT=1 running validator eval → None.

    Uses OTHER_HOTKEY so the agent is not excluded by already_evaluated,
    isolating the count limit condition.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    # 'running_agent' is not 'finished' or 'error' → computed_status='running'
    await _insert_run(eval_id, status="running_agent")
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_at_finished_eval_limit():
    """Agent already has NUM_EVALS_PER_AGENT=1 finished validator eval → None.

    Uses OTHER_HOTKEY to isolate the count limit from already_evaluated.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(eval_id, status="finished")  # computed_status='success'
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_failed_evals_dont_consume_slots():
    """A failed validator eval (error outside 1000-1999) is excluded by the lateral ON condition.

    The agent should still be returned because the failed eval does not count
    toward num_running_evals or num_finished_evals.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    # error_code=9000 is outside 1000-1999 → computed_status='failure' → excluded by ON condition.
    await _insert_run(eval_id, status="error", error_code=9000)
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_id


@pytest.mark.anyio
async def test_validator_candidates_use_each_competitions_required_count():
    await _insert_eval_set(1)
    await _insert_eval_set(2)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=2, required_validator_count=2)

    full_agent = await _insert_agent(miner_hotkey="full-agent", set_id=1)
    available_agent = await _insert_agent(miner_hotkey="available-agent", set_id=2)
    for agent_id, set_id in ((full_agent, 1), (available_agent, 2)):
        evaluation_id = await _insert_evaluation(
            agent_id,
            group="validator",
            validator_hotkey=OTHER_HOTKEY,
            set_id=set_id,
        )
        await _insert_run(evaluation_id, status="finished")

    assert await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY) == available_agent


@pytest.mark.anyio
async def test_validator_candidates_ignore_grandfathered_cross_set_evaluations():
    await _insert_eval_set(1)
    await _insert_eval_set(2)
    agent_id = await _insert_agent(set_id=1)

    async with _db.pool.acquire() as conn:
        await conn.execute("ALTER TABLE evaluations DISABLE TRIGGER ALL")
        try:
            evaluation_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO evaluations (
                    evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group
                ) VALUES ($1, $2, $3, 2, 'validator')
                """,
                evaluation_id,
                agent_id,
                HOTKEY,
            )
        finally:
            await conn.execute("ALTER TABLE evaluations ENABLE TRIGGER ALL")
    await _insert_run(evaluation_id, status="finished")

    assert await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY) == agent_id


@pytest.mark.anyio
async def test_ordering_by_screener_2_score_uses_verifier_reward():
    """verifier_reward drives screener_2_score ordering, not test_results parsing.

    Agent A: verifier_reward=1.0 (solved=true)  → score=1.0, created AFTER agent B
    Agent B: verifier_reward=0.0 (solved=false) → score=0.0, test_results=[pass]

    Expect agent A returned first because score 1.0 > 0.0 despite being created later.
    """
    # Agent B created first — so created_at tiebreak alone would favour B
    now = datetime.now(timezone.utc)
    agent_b = await _insert_agent(created_at=now - timedelta(seconds=10))
    agent_a = await _insert_agent(created_at=now)

    eval_a = await _insert_evaluation(agent_a, group="screener_2", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(eval_a, status="finished", verifier_reward=1.0)

    eval_b = await _insert_evaluation(agent_b, group="screener_2", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(
        eval_b,
        status="finished",
        verifier_reward=0.0,
        test_results=[{"status": "pass", "name": "t1"}],
    )

    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_a


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_returns_agents_own_set():
    await _insert_eval_set(set_id=1)
    await _insert_eval_set(set_id=2)
    agent_id = await _insert_agent(status="finished")
    # Agent's score lives in the OLDER set, not the latest.
    await _insert_agent_score(agent_id=agent_id, set_id=1, final_score=0.842123456)

    result = await get_agent_score_and_set_id(agent_id)
    assert result == (1, 0.842123)


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_returns_none_for_unscored_agent():
    await _insert_eval_set(set_id=1)
    agent_id = await _insert_agent(status="finished")

    result = await get_agent_score_and_set_id(agent_id)
    assert result is None


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_excludes_benchmark_agents():
    await _insert_eval_set(set_id=1)
    agent_id = await _insert_agent(status="finished")
    await _insert_agent_score(agent_id=agent_id, set_id=1, final_score=0.9)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, 'benchmark')",
            agent_id,
        )

    result = await get_agent_score_and_set_id(agent_id)
    assert result is None
