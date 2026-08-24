from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from models.agent import Agent, PublicAgent
from queries import scores as score_queries
from queries.agent import (
    get_latest_agent_for_miner_hotkey,
    get_latest_public_agent_for_miner_hotkey,
    get_top_agents,
)
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.evaluation import (
    get_approved_leader_ranking_for_set,
    get_approved_validator_leader_score_for_set,
    get_top_agent_score_for_set,
    get_validator_agent_score_for_set,
)
from queries.scores import (
    get_incentive_reward_candidates,
    get_weight_calculation_snapshot,
    get_weight_receiving_agent_hotkey,
    get_weight_receiving_agent_info,
)

SET_ID = 23
SET_CREATED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)
TRUNCATE_SCORE_TEST_TABLES = (
    "TRUNCATE evaluation_runs, evaluations, agent_scores, evaluation_sets, competitions, "
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


async def _insert_eval_set(conn, *, set_id: int = SET_ID, problem_name: str = "problem-a") -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
        VALUES ($1, 'validator', $2, $3)
        """,
        set_id,
        problem_name,
        SET_CREATED_AT,
    )


async def _configure_weight_competition(
    conn,
    *,
    set_id: int,
    raw_weight: float,
    incentive_enabled: bool,
    required_validator_count: int,
    half_life_hours: float,
) -> None:
    await conn.execute("INSERT INTO competitions (set_id) VALUES ($1) ON CONFLICT (set_id) DO NOTHING", set_id)
    await conn.execute(
        """
        UPDATE competitions
        SET start_date = $2,
            is_paused = false,
            emissions_end_at = NULL,
            end_date = NULL,
            raw_emission_weight = $3,
            scoring_mode = 'consensus',
            screener_1_threshold = 0.3,
            screener_2_threshold = 0.4,
            prune_threshold = 0.9,
            required_validator_count = $4,
            pre_screening_enabled = true,
            auto_approval_enabled = true,
            hardcoding_policy_version = 'hardcoding-v1',
            incentive_enabled = $5,
            incentive_performance_threshold = 0.03,
            incentive_cost_threshold = 0.06,
            incentive_reward_half_life_hours = $6,
            incentive_time_multiplier_scale_hours = 12
        WHERE set_id = $1
        """,
        set_id,
        SET_CREATED_AT,
        Decimal(str(raw_weight)),
        required_validator_count,
        incentive_enabled,
        Decimal(str(half_life_hours)),
    )


async def _insert_scored_agent(
    conn,
    *,
    miner_hotkey: str,
    final_score: float,
    cost_usd: float,
    approved: bool = True,
    approved_at: datetime | None = None,
    relative_improvement_units: float | None = 1,
    time_multiplier: float | None = 1,
    initial_reward_score: float | None = None,
    created_at: datetime,
    miner_coldkey: str | None = None,
    status: str = "finished",
    set_id: int = SET_ID,
    validator_count: int = 1,
) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, miner_coldkey, name, version_num,
            status, created_at, ip_address, set_id
        )
        VALUES ($1, $2, $3, $4, 0, $6, $5, '127.0.0.1', $7)
        """,
        agent_id,
        miner_hotkey,
        miner_coldkey,
        miner_hotkey,
        created_at,
        status,
        set_id,
    )
    evaluation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at)
        VALUES ($1, $2, 'validator-hotkey', $3, 'validator', $4)
        """,
        evaluation_id,
        agent_id,
        set_id,
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
            INSERT INTO approved_agents (
                agent_id, set_id, approved_at,
                relative_improvement_units, time_multiplier, initial_reward_score
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (agent_id, set_id) DO UPDATE
            SET approved_at = EXCLUDED.approved_at,
                relative_improvement_units = EXCLUDED.relative_improvement_units,
                time_multiplier = EXCLUDED.time_multiplier,
                initial_reward_score = EXCLUDED.initial_reward_score
            """,
            agent_id,
            set_id,
            approved_at,
            relative_improvement_units,
            time_multiplier,
            initial_reward_score,
        )
    await conn.execute(
        """
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        )
        VALUES ($1, $2, $3, 0, $4, $5, $6, $7, $8, $10, $9)
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
        set_id,
        approved,
        approved_at,
        final_score,
        validator_count,
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
            initial_reward_score=0.40,
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        same_coldkey_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="same-coldkey-hotkey",
            miner_coldkey="old-coldkey",
            final_score=0.495,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.20,
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )
        owner_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="owner-hotkey",
            final_score=0.49,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.20,
            created_at=SET_CREATED_AT + timedelta(hours=3),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="banned-hotkey",
            miner_coldkey="banned-coldkey",
            final_score=0.60,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.30,
            created_at=SET_CREATED_AT + timedelta(hours=5),
        )

    await ban_coldkey("banned-coldkey", "test ban")

    candidates, observed_at = await get_incentive_reward_candidates(SET_ID, 1)
    by_id = {candidate.agent_id: candidate for candidate in candidates}

    assert set(by_id) == {old_agent_id, same_coldkey_agent_id, owner_agent_id}
    assert by_id[old_agent_id].initial_reward_score == pytest.approx(0.40)
    assert observed_at >= now


@pytest.mark.anyio
async def test_active_incentive_candidates_fail_when_an_eligible_agent_has_no_snapshot():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="snapshot-hotkey",
            final_score=0.55,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.40,
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        missing_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="missing-snapshot-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    with pytest.raises(ValueError, match=str(missing_agent_id)):
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
async def test_current_approved_leader_can_be_selected_without_exclusion():
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
            miner_hotkey="unapproved-hotkey",
            final_score=0.90,
            cost_usd=0.01,
            approved=False,
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    leader = await get_approved_leader_ranking_for_set(SET_ID, required_validator_count=1)

    assert leader is not None
    assert leader.agent_id == leader_id
    assert leader.final_score == 0.50
    assert leader.avg_cost_usd == pytest.approx(0.10)
    assert leader.approved_at == now - timedelta(hours=1)
    assert leader.observed_at is not None


@pytest.mark.anyio
async def test_operational_score_queries_ignore_grandfathered_cross_set_score() -> None:
    now = datetime.now(timezone.utc)
    cross_set_agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        matching_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="matching-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await conn.execute("INSERT INTO competitions (set_id) VALUES ($1)", SET_ID + 1)
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
            ) VALUES ($1, 'cross-set-hotkey', 'cross-set', 1, 'finished', NOW(), '127.0.0.1', $2)
            """,
            cross_set_agent_id,
            SET_ID + 1,
        )
        # Recreate a score row that predates the prospective membership FK.
        async with conn.transaction():
            await conn.execute("ALTER TABLE agent_scores DISABLE TRIGGER ALL")
            await conn.execute(
                """
                INSERT INTO agent_scores (
                    agent_id, miner_hotkey, name, version_num, created_at, status,
                    set_id, approved, approved_at, validator_count, final_score
                ) VALUES ($1, 'cross-set-hotkey', 'cross-set', 1, NOW(), 'finished',
                          $2, true, $3, 1, 0.99)
                """,
                cross_set_agent_id,
                SET_ID,
                now - timedelta(hours=1),
            )
            await conn.execute("ALTER TABLE agent_scores ENABLE TRIGGER ALL")

    assert await get_top_agent_score_for_set(SET_ID) == pytest.approx(0.50)
    assert await get_approved_validator_leader_score_for_set(SET_ID, uuid4(), 1) == pytest.approx(0.50)

    leader = await get_approved_leader_ranking_for_set(SET_ID, required_validator_count=1)
    assert leader is not None
    assert leader.agent_id == matching_agent_id
    assert leader.final_score == pytest.approx(0.50)
    assert leader.avg_cost_usd == pytest.approx(0.10)

    assert await get_validator_agent_score_for_set(cross_set_agent_id, SET_ID, 1) is None
    candidate = await get_validator_agent_score_for_set(matching_agent_id, SET_ID, 1)
    assert candidate is not None
    assert candidate.agent_id == matching_agent_id
    assert candidate.final_score == pytest.approx(0.50)
    assert candidate.avg_cost_usd == pytest.approx(0.10)


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
        # Recreate a pre-migration cross-set review row. New rows are protected
        # by the composite membership constraint.
        async with conn.transaction():
            await conn.execute("ALTER TABLE agent_approval_states DISABLE TRIGGER ALL")
            await conn.execute(
                """
                INSERT INTO agent_approval_states (
                    agent_id, set_id, processing_status, system_verdict, published_verdict
                ) VALUES ($1, $2, 'completed', 'rejected', 'rejected')
                """,
                eligible_id,
                SET_ID - 1,
            )
            await conn.execute("ALTER TABLE agent_approval_states ENABLE TRIGGER ALL")

    await ban_coldkey("banned-leader-coldkey", "test ban")
    agents = await get_top_agents()
    assert [agent.agent_id for agent in agents] == [eligible_id]
    assert agents[0].competition_state is not None
    assert agents[0].competition_state.relative_improvement_units == 1
    assert agents[0].competition_state.time_multiplier == 1
    assert agents[0].competition_state.status == "baseline"
    assert agents[0].competition_state.set_id == SET_ID

    agent_detail = await get_latest_public_agent_for_miner_hotkey("eligible-second-hotkey")
    assert agent_detail is not None
    assert isinstance(agent_detail, PublicAgent)
    assert agent_detail.competition_state is not None
    assert agent_detail.competition_state.relative_improvement_units == 1
    assert agent_detail.competition_state.time_multiplier == 1
    assert agent_detail.competition_state.status == "baseline"
    assert agent_detail.competition_state.set_id == SET_ID
    assert agent_detail.competition_state == agents[0].competition_state

    core_agent = await get_latest_agent_for_miner_hotkey("eligible-second-hotkey")
    assert core_agent is not None
    assert type(core_agent) is Agent
    assert "competition_state" not in core_agent.model_dump()
    assert "approved" not in core_agent.model_dump()

    await unban_coldkey("banned-leader-coldkey")
    assert [agent.miner_hotkey for agent in await get_top_agents()] == [
        "banned-leader-hotkey",
        "eligible-second-hotkey",
    ]


@pytest.mark.anyio
async def test_top_agents_excludes_review_rejected_agent_with_approval_snapshot():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        rejected_id = await _insert_scored_agent(
            conn,
            miner_hotkey="rejected-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved=True,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        eligible_id = await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-hotkey",
            final_score=0.49,
            cost_usd=0.10,
            approved=False,
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )
        await conn.execute(
            """
            INSERT INTO agent_approval_states (
                agent_id, set_id, processing_status, system_verdict, published_verdict
            ) VALUES ($1, $2, 'completed', 'rejected', 'rejected')
            """,
            rejected_id,
            SET_ID,
        )

    agents = await get_top_agents()

    assert [agent.agent_id for agent in agents] == [eligible_id]


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


@pytest.mark.anyio
async def test_weight_snapshot_uses_explicit_set_policy_counts_half_lives_and_membership() -> None:
    now = datetime.now(timezone.utc)
    second_set_id = SET_ID + 1
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_eval_set(conn, set_id=second_set_id, problem_name="problem-b")
        await _configure_weight_competition(
            conn,
            set_id=SET_ID,
            raw_weight=0.6,
            incentive_enabled=True,
            required_validator_count=2,
            half_life_hours=100,
        )
        await _configure_weight_competition(
            conn,
            set_id=second_set_id,
            raw_weight=0.4,
            incentive_enabled=True,
            required_validator_count=1,
            half_life_hours=200,
        )
        first_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="first-set-hotkey",
            final_score=0.5,
            cost_usd=0.1,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.6,
            created_at=SET_CREATED_AT + timedelta(hours=1),
            set_id=SET_ID,
            validator_count=2,
        )
        second_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="second-set-hotkey",
            final_score=0.5,
            cost_usd=0.1,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.4,
            created_at=SET_CREATED_AT + timedelta(hours=2),
            set_id=second_set_id,
            validator_count=1,
        )
        cross_set_agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="grandfathered-cross-set",
            final_score=0.9,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=1.0,
            created_at=SET_CREATED_AT + timedelta(hours=3),
            set_id=second_set_id,
            validator_count=2,
        )
        async with conn.transaction():
            await conn.execute("ALTER TABLE approved_agents DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE agent_scores DISABLE TRIGGER ALL")
            await conn.execute(
                """
                INSERT INTO approved_agents (
                    agent_id, set_id, approved_at,
                    relative_improvement_units, time_multiplier, initial_reward_score
                ) VALUES ($1, $2, $3, 1, 1, 1)
                ON CONFLICT (agent_id, set_id) DO NOTHING
                """,
                cross_set_agent_id,
                SET_ID,
                now - timedelta(hours=1),
            )
            await conn.execute(
                "UPDATE agent_scores SET set_id = $2, validator_count = 2 WHERE agent_id = $1",
                cross_set_agent_id,
                SET_ID,
            )
            await conn.execute("ALTER TABLE agent_scores ENABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE approved_agents ENABLE TRIGGER ALL")

    snapshot = await get_weight_calculation_snapshot()
    by_set = {competition.set_id: competition for competition in snapshot.competitions}

    assert by_set[SET_ID].policy is not None
    assert by_set[SET_ID].policy.required_validator_count == 2
    assert by_set[SET_ID].policy.incentive_reward_half_life_hours == 100
    assert [candidate.agent_id for candidate in by_set[SET_ID].incentive_candidates] == [first_agent_id]
    assert by_set[second_set_id].policy is not None
    assert by_set[second_set_id].policy.required_validator_count == 1
    assert by_set[second_set_id].policy.incentive_reward_half_life_hours == 200
    assert [candidate.agent_id for candidate in by_set[second_set_id].incentive_candidates] == [second_agent_id]


@pytest.mark.anyio
async def test_legacy_weight_snapshot_uses_the_explicit_competition_not_the_highest_set() -> None:
    now = datetime.now(timezone.utc)
    higher_set_id = SET_ID + 1
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_eval_set(conn, set_id=higher_set_id, problem_name="problem-b")
        await _configure_weight_competition(
            conn,
            set_id=SET_ID,
            raw_weight=1,
            incentive_enabled=False,
            required_validator_count=1,
            half_life_hours=100,
        )
        await _configure_weight_competition(
            conn,
            set_id=higher_set_id,
            raw_weight=0,
            incentive_enabled=False,
            required_validator_count=1,
            half_life_hours=100,
        )
        expected_id = await _insert_scored_agent(
            conn,
            miner_hotkey="explicit-set-leader",
            final_score=0.5,
            cost_usd=0.1,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
            set_id=SET_ID,
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="higher-set-leader",
            final_score=0.9,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
            set_id=higher_set_id,
        )

    snapshot = await get_weight_calculation_snapshot()
    current = next(competition for competition in snapshot.competitions if competition.set_id == SET_ID)

    assert current.legacy_receiver is not None
    assert current.legacy_receiver.agent_id == expected_id
    assert current.legacy_receiver.miner_hotkey == "explicit-set-leader"


@pytest.mark.anyio
async def test_weight_snapshot_is_one_repeatable_read_view(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _configure_weight_competition(
            conn,
            set_id=SET_ID,
            raw_weight=0.5,
            incentive_enabled=True,
            required_validator_count=1,
            half_life_hours=100,
        )
        agent_id = await _insert_scored_agent(
            conn,
            miner_hotkey="snapshot-agent",
            final_score=0.5,
            cost_usd=0.1,
            approved_at=now - timedelta(hours=1),
            initial_reward_score=0.4,
            created_at=SET_CREATED_AT + timedelta(hours=1),
            miner_coldkey="snapshot-coldkey",
        )

    candidate_read_reached = asyncio.Event()
    allow_candidate_read = asyncio.Event()
    original_candidate_read = score_queries._get_incentive_reward_candidates

    async def paused_candidate_read(*args, **kwargs):
        candidate_read_reached.set()
        await allow_candidate_read.wait()
        return await original_candidate_read(*args, **kwargs)

    monkeypatch.setattr(score_queries, "_get_incentive_reward_candidates", paused_candidate_read)
    snapshot_task = asyncio.create_task(get_weight_calculation_snapshot())
    await asyncio.wait_for(candidate_read_reached.wait(), timeout=2)

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET raw_emission_weight = 0.9 WHERE set_id = $1", SET_ID)
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ('snapshot-coldkey', 'test')"
        )

    allow_candidate_read.set()
    first_snapshot = await asyncio.wait_for(snapshot_task, timeout=2)
    first = next(competition for competition in first_snapshot.competitions if competition.set_id == SET_ID)
    assert first.raw_emission_weight == Decimal("0.5")
    assert first.incentive_candidates[0].agent_id == agent_id

    second_snapshot = await get_weight_calculation_snapshot()
    second = next(competition for competition in second_snapshot.competitions if competition.set_id == SET_ID)
    assert second.raw_emission_weight == Decimal("0.9")
    assert second.incentive_candidates == ()
