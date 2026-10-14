import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import api.config as config
import utils.database as _db
from queries.approval import project_next_approval_job_state

SET_ID = 72


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db, monkeypatch):
    monkeypatch.setattr(config, "INCENTIVE_START_SET_ID", SET_ID)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE approval_job_rounds, agent_approval_states, approval_jobs, approved_agents, agent_scores, "
            "banned_coldkeys, benchmark_agent_ids, benchmark_phase_entries, competition_benchmark_phases, agents, "
            "competitions RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            """
            INSERT INTO competitions (
                set_id, start_date, scoring_mode, screener_1_threshold, screener_2_threshold,
                prune_threshold, required_validator_count, max_concurrent_evaluation_runs, pre_screening_enabled,
                auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
                incentive_performance_threshold, incentive_cost_threshold,
                incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
            ) VALUES ($1, NOW() - INTERVAL '10 days', 'consensus', 0.4, 0.4, 0.4, $2, 8, true, true,
                      'hardcoding-v1', true, $3, $4, 336, $5)
            """,
            SET_ID,
            config.NUM_EVALS_PER_AGENT,
            config.INCENTIVE_PERFORMANCE_THRESHOLD,
            config.INCENTIVE_COST_THRESHOLD,
            config.INCENTIVE_TIME_MULTIPLIER_SCALE_HOURS,
        )
    yield


async def _insert_agent_score(
    conn,
    *,
    hotkey: str,
    coldkey: str,
    final_score: float,
    approved: bool,
    approved_at: datetime | None = None,
    initial_reward_score: float | None = None,
    status: str = "finished",
) -> UUID:
    agent_id = uuid4()
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, miner_coldkey, name, version_num,
            status, created_at, ip_address, set_id
        ) VALUES ($1, $2, $3, $2, 1, $5, $4, '127.0.0.1', $6)
        """,
        agent_id,
        hotkey,
        coldkey,
        created_at,
        status,
        SET_ID,
    )
    if approved:
        await conn.execute(
            """
            INSERT INTO approved_agents (
                agent_id, set_id, approved_at, relative_improvement_units, time_multiplier, initial_reward_score
            ) VALUES ($1, $2, $3, 1, 1, $4)
            """,
            agent_id,
            SET_ID,
            approved_at,
            initial_reward_score,
        )
    await conn.execute(
        """
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        ) VALUES ($1, $2, $2, 1, $3, $4, $5, $6, $7, $8, $9)
        """,
        agent_id,
        hotkey,
        created_at,
        status,
        SET_ID,
        approved,
        approved_at,
        config.NUM_EVALS_PER_AGENT,
        final_score,
    )
    return agent_id


async def _insert_completed_approval_job(conn, agent_id: UUID) -> None:
    await conn.execute(
        """
        INSERT INTO approval_jobs (
            job_id, agent_id, set_id, status, policy_version, input_snapshot,
            aggregate_verdict, aggregate_score, aggregate_confidence,
            aggregate_summary, decision_source
        ) VALUES (
            $1, $2, $3, 'completed', 'approval-v1', '{}'::jsonb,
            'approved', 0.9, 0.9, 'judge approved', 'auto_judge'
        )
        """,
        uuid4(),
        agent_id,
        SET_ID,
    )


async def _start_phase(conn) -> UUID:
    return await conn.fetchval(
        "INSERT INTO competition_benchmark_phases (set_id, started_by, start_reason) "
        "VALUES ($1, 'admin', 'launch') RETURNING phase_id",
        SET_ID,
    )


async def _end_phase(conn, phase_id: UUID, ended_at: datetime) -> None:
    await conn.execute(
        "UPDATE competition_benchmark_phases SET ended_at = $2, ended_by = 'admin', end_reason = 'done' "
        "WHERE phase_id = $1",
        phase_id,
        ended_at,
    )


@pytest.mark.anyio
async def test_approved_verdict_during_a_phase_joins_the_pool_instead_of_approving() -> None:
    async with _db.pool.acquire() as conn:
        phase_id = await _start_phase(conn)
        candidate_id = await _insert_agent_score(conn, hotkey="hk", coldkey="ck", final_score=0.4, approved=False)
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approvals = await conn.fetchval("SELECT count(*) FROM approved_agents WHERE agent_id = $1", candidate_id)
        entry_phase = await conn.fetchval(
            "SELECT phase_id FROM benchmark_phase_entries WHERE agent_id = $1", candidate_id
        )
        verdict = await conn.fetchval(
            "SELECT system_verdict FROM agent_approval_states WHERE agent_id = $1", candidate_id
        )
        review = await conn.fetchval(
            "SELECT approval_review_status FROM agent_final_review_statuses WHERE agent_id = $1", candidate_id
        )
    assert approvals == 0
    assert entry_phase == phase_id
    assert verdict == "approved"
    assert review == "benchmark_pool"


@pytest.mark.anyio
async def test_owner_baseline_skips_the_hold(monkeypatch) -> None:
    """Passes before and after the hold exists: it guards the owner exemption."""
    monkeypatch.setattr(config, "OWNER_HOTKEY", "owner-hk")
    async with _db.pool.acquire() as conn:
        await _start_phase(conn)
        baseline_id = await _insert_agent_score(
            conn, hotkey="owner-hk", coldkey="owner-ck", final_score=0.3, approved=False
        )
        await _insert_completed_approval_job(conn, baseline_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        reward = await conn.fetchval(
            "SELECT initial_reward_score FROM approved_agents WHERE agent_id = $1", baseline_id
        )
        entries = await conn.fetchval("SELECT count(*) FROM benchmark_phase_entries")
    assert reward == 0.0
    assert entries == 0


@pytest.mark.anyio
async def test_unfinished_candidate_is_still_rejected_during_a_phase() -> None:
    async with _db.pool.acquire() as conn:
        await _start_phase(conn)
        candidate_id = await _insert_agent_score(
            conn, hotkey="hk", coldkey="ck", final_score=0.4, approved=False, status="evaluating"
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM benchmark_phase_entries") == 0
        assert await conn.fetchval("SELECT count(*) FROM approved_agents") == 0


@pytest.mark.anyio
async def test_after_a_phase_the_bonus_clock_starts_at_the_phase_end() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        phase_id = await _start_phase(conn)
        await _end_phase(conn, phase_id, now - timedelta(hours=1))
        leader_id = await _insert_agent_score(
            conn,
            hotkey="leader-hk",
            coldkey="leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_reward_score=0.25,
        )
        candidate_id = await _insert_agent_score(
            conn, hotkey="candidate-hk", coldkey="candidate-ck", final_score=0.52, approved=False
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)
    assert approved["baseline_agent_id"] == leader_id
    assert approved["benchmark_phase_id"] is None
    expected = 1 + math.sqrt(1 / config.INCENTIVE_TIME_MULTIPLIER_SCALE_HOURS)
    assert approved["time_multiplier"] == pytest.approx(expected, rel=1e-2)
