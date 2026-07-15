from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.agent import get_top_agents
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.evaluation import get_approved_leader_ranking_for_set, get_approved_validator_leader_score_for_set
from queries.scores import (
    get_incentive_reward_candidates,
    get_weight_receiving_agent_hotkey,
    get_weight_receiving_agent_info,
)
from queries.statistics import score_improvement_24_hrs, top_score

SET_ID = 23
SET_CREATED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)
TRUNCATE_SCORE_TEST_TABLES = (
    "TRUNCATE evaluation_runs, evaluations, agent_scores, evaluation_sets, "
    "benchmark_agent_ids, banned_coldkeys, banned_hotkeys, agents RESTART IDENTITY CASCADE"
)

# TODO: Add more edge cases to scoring tests


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(TRUNCATE_SCORE_TEST_TABLES)
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(TRUNCATE_SCORE_TEST_TABLES)


async def _insert_eval_set(conn) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
        VALUES ($1, 'validator', 'problem-a', $2)
        """,
        SET_ID,
        SET_CREATED_AT,
    )


async def _insert_scored_agent(
    conn,
    *,
    miner_hotkey: str,
    final_score: float,
    cost_usd: float,
    approved: bool = True,
    approved_at: datetime | None = None,
    initial_improvement_bonus: float | None = None,
    created_at: datetime,
    miner_coldkey: str | None = None,
    status: str = "finished",
) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num, status, created_at, ip_address)
        VALUES ($1, $2, $3, $4, 0, $6, $5, '127.0.0.1')
        """,
        agent_id,
        miner_hotkey,
        miner_coldkey,
        miner_hotkey,
        created_at,
        status,
    )
    evaluation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at)
        VALUES ($1, $2, 'validator-hotkey', $3, 'validator', $4)
        """,
        evaluation_id,
        agent_id,
        SET_ID,
        created_at,
    )
    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id, evaluation_id, problem_name, status, created_at,
            started_running_agent_at, finished_or_errored_at, verifier_reward, cost_usd
        )
        VALUES ($1, $2, 'problem-a', 'finished', $3, $3, $4, 1.0, $5)
        """,
        uuid4(),
        evaluation_id,
        created_at,
        created_at + timedelta(seconds=60),
        cost_usd,
    )
    if approved and approved_at is not None:
        await conn.execute(
            """
            INSERT INTO approved_agents (agent_id, set_id, approved_at, initial_improvement_bonus)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (agent_id, set_id) DO UPDATE
            SET approved_at = EXCLUDED.approved_at,
                initial_improvement_bonus = EXCLUDED.initial_improvement_bonus
            """,
            agent_id,
            SET_ID,
            approved_at,
            initial_improvement_bonus,
        )
    await conn.execute(
        """
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        )
        VALUES ($1, $2, $3, 0, $4, $5, $6, $7, $8, 1, $9)
        ON CONFLICT (agent_id) DO UPDATE
        SET
            miner_hotkey = EXCLUDED.miner_hotkey,
            name = EXCLUDED.name,
            version_num = EXCLUDED.version_num,
            created_at = EXCLUDED.created_at,
            status = EXCLUDED.status,
            set_id = EXCLUDED.set_id,
            approved = EXCLUDED.approved,
            approved_at = EXCLUDED.approved_at,
            validator_count = EXCLUDED.validator_count,
            final_score = EXCLUDED.final_score
        """,
        agent_id,
        miner_hotkey,
        miner_hotkey,
        created_at,
        status,
        SET_ID,
        approved,
        approved_at,
        final_score,
    )
    return agent_id


@pytest.mark.anyio
async def test_active_incentive_candidates_use_snapshots_without_legacy_expiry():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        old_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="old-hotkey",
            miner_coldkey="old-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(days=30),
            initial_improvement_bonus=0.40,
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        same_coldkey_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="same-coldkey-hotkey",
            miner_coldkey="old-coldkey",
            final_score=0.495,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.20,
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )
        owner_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="owner-hotkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.20,
            created_at=SET_CREATED_AT + timedelta(hours=3),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="banned-hotkey",
            miner_coldkey="banned-coldkey",
            final_score=0.60,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.30,
            created_at=SET_CREATED_AT + timedelta(hours=5),
        )

    await ban_coldkey("banned-coldkey", "test ban")

    candidates, observed_at = await get_incentive_reward_candidates(SET_ID, 1)
    by_id = {candidate.agent_id: candidate for candidate in candidates}

    assert set(by_id) == {old_agent_id, same_coldkey_agent_id, owner_agent_id}
    assert by_id[old_agent_id].initial_improvement_bonus == pytest.approx(0.40)
    assert observed_at >= now


@pytest.mark.anyio
async def test_active_incentive_candidates_require_snapshots():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="missing-snapshot-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )

    with pytest.raises(
        RuntimeError,
        match=rf"Active incentive set {SET_ID} has approved agents without incentive snapshots: {agent_id}",
    ):
        await get_incentive_reward_candidates(SET_ID, 1)


@pytest.mark.anyio
async def test_weight_receiver_is_top_scored_agent_when_eligible():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        leader_id = await _insert_scored_agent(
            conn,
            miner_hotkey="leader-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="second-hotkey",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() == "leader-hotkey"

    info = await get_weight_receiving_agent_info()
    assert info is not None
    assert info["miner_hotkey"] == "leader-hotkey"
    assert info["agent_id"] == leader_id


@pytest.mark.anyio
async def test_banned_coldkey_is_skipped_for_incentive():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="banned-leader-hotkey",
            miner_coldkey="banned-leader-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        second_id = await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second-hotkey",
            miner_coldkey="eligible-second-coldkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    await ban_coldkey("banned-leader-coldkey", "test ban")

    assert await get_weight_receiving_agent_hotkey() == "eligible-second-hotkey"
    assert await top_score() == 0.49
    assert await score_improvement_24_hrs() == 0
    info = await get_weight_receiving_agent_info()
    assert info is not None
    assert info["agent_id"] == second_id

    await unban_coldkey("banned-leader-coldkey")
    assert await get_weight_receiving_agent_hotkey() == "banned-leader-hotkey"


@pytest.mark.anyio
async def test_top_agents_uses_coldkey_bans_at_read_time():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="banned-leader-hotkey",
            miner_coldkey="banned-leader-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        eligible_id = await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second-hotkey",
            miner_coldkey="eligible-second-coldkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    await ban_coldkey("banned-leader-coldkey", "test ban")
    assert [agent.agent_id for agent in await get_top_agents()] == [eligible_id]

    await unban_coldkey("banned-leader-coldkey")
    assert [agent.miner_hotkey for agent in await get_top_agents()] == [
        "banned-leader-hotkey",
        "eligible-second-hotkey",
    ]


@pytest.mark.anyio
async def test_legacy_hotkey_ban_does_not_remove_top_agent_or_delete_score():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        leader_id = await _insert_scored_agent(
            conn,
            miner_hotkey="legacy-banned-hotkey",
            miner_coldkey="eligible-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await conn.execute(
            "INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) VALUES ('legacy-banned-hotkey', 'legacy ban')"
        )

    assert [agent.agent_id for agent in await get_top_agents()] == [leader_id]


@pytest.mark.anyio
async def test_banned_coldkey_is_not_used_as_validator_leader_bar():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="banned-leader-hotkey",
            miner_coldkey="banned-leader-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second-hotkey",
            miner_coldkey="eligible-second-coldkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    await ban_coldkey("banned-leader-coldkey", "test ban")

    excluded_agent_id = uuid4()
    assert await get_approved_validator_leader_score_for_set(SET_ID, excluded_agent_id, 1) == 0.49
    leader = await get_approved_leader_ranking_for_set(SET_ID, excluded_agent_id, 1)
    assert leader is not None
    assert leader.final_score == 0.49


@pytest.mark.anyio
async def test_review_rejected_agent_is_not_used_as_validator_leader_bar():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        rejected_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="rejected-leader-hotkey",
            miner_coldkey="rejected-leader-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await conn.execute(
            """
            INSERT INTO agent_approval_states (
                agent_id, set_id, processing_status, system_verdict, published_verdict
            ) VALUES ($1, $2, 'completed', 'rejected', 'rejected')
            """,
            rejected_agent_id,
            SET_ID,
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second-hotkey",
            miner_coldkey="eligible-second-coldkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_approved_validator_leader_score_for_set(SET_ID, uuid4(), 1) == 0.49


@pytest.mark.anyio
async def test_non_finished_agent_is_not_used_as_validator_leader_bar():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="evaluating-leader-hotkey",
            miner_coldkey="evaluating-leader-coldkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
            status="evaluating",
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second-hotkey",
            miner_coldkey="eligible-second-coldkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    excluded_agent_id = uuid4()
    assert await get_approved_validator_leader_score_for_set(SET_ID, excluded_agent_id, 1) == 0.49
    leader = await get_approved_leader_ranking_for_set(SET_ID, excluded_agent_id, 1)
    assert leader is not None
    assert leader.final_score == 0.49


@pytest.mark.anyio
async def test_expired_top_scored_agent_burns_instead_of_falling_through():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="expired-leader",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=13),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() is None
    assert await get_weight_receiving_agent_info() is None


@pytest.mark.anyio
async def test_unapproved_top_scored_agent_is_skipped():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="unapproved-leader",
            final_score=0.50,
            cost_usd=0.10,
            approved=False,
            approved_at=None,
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() == "eligible-second"


@pytest.mark.anyio
async def test_tied_scores_apply_cost_tiebreak_before_eligibility_window():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="expired-cheaper-leader",
            final_score=0.4489795918367347,
            cost_usd=0.06,
            approved_at=now - timedelta(hours=13),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-costlier-second",
            final_score=0.4489795918367347,
            cost_usd=0.08,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() is None
