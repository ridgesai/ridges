import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import api.config as config
import utils.database as _db
from queries.approval import project_next_approval_job_state
from queries.banned_coldkey import COLDKEY_BAN_LOCK_NAMESPACE

SET_ID = 71


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db, monkeypatch):
    monkeypatch.setattr(config, "INCENTIVE_START_SET_ID", SET_ID)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE approval_job_rounds, agent_approval_states, approval_jobs, approved_agents, "
            "agent_scores, banned_coldkeys, benchmark_agent_ids, agents RESTART IDENTITY CASCADE"
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
    initial_improvement_bonus: float | None = None,
    status: str = "finished",
    validator_count: int = config.NUM_EVALS_PER_AGENT,
) -> UUID:
    agent_id = uuid4()
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, miner_coldkey, name, version_num,
            status, created_at, ip_address
        ) VALUES ($1, $2, $3, $2, 1, $5, $4, '127.0.0.1')
        """,
        agent_id,
        hotkey,
        coldkey,
        created_at,
        status,
    )
    if approved:
        await conn.execute(
            """
            INSERT INTO approved_agents (
                agent_id, set_id, approved_at, raw_improvement, time_multiplier, initial_improvement_bonus
            ) VALUES ($1, $2, $3, 1, 1, $4)
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
        ) VALUES ($1, $2, $2, 1, $3, $4, $5, $6, $7, $8, $9)
        """,
        agent_id,
        hotkey,
        created_at,
        status,
        SET_ID,
        approved,
        approved_at,
        validator_count,
        final_score,
    )
    return agent_id


async def _mark_review_rejected(conn, agent_id: UUID) -> None:
    await conn.execute(
        """
        INSERT INTO agent_approval_states (
            agent_id,
            set_id,
            processing_status,
            system_verdict,
            published_verdict
        ) VALUES ($1, $2, 'completed', 'rejected', 'rejected')
        """,
        agent_id,
        SET_ID,
    )


async def _insert_completed_approval_job(conn, agent_id: UUID) -> UUID:
    job_id = uuid4()
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
        job_id,
        agent_id,
        SET_ID,
    )
    return job_id


@pytest.mark.anyio
async def test_projector_stores_time_adjusted_incentive_snapshot() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        leader_id = await _insert_agent_score(
            conn,
            hotkey="leader-hk",
            coldkey="leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_improvement_bonus=0.25,
        )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.52,
            approved=False,
        )
        job_id = await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)
        state = await conn.fetchrow("SELECT * FROM agent_approval_states WHERE agent_id = $1", candidate_id)
        projected_at = await conn.fetchval("SELECT projected_at FROM approval_jobs WHERE job_id = $1", job_id)

    assert approved is not None
    assert approved["baseline_agent_id"] == leader_id
    assert approved["performance_delta"] == pytest.approx(0.04)
    assert approved["raw_improvement"] == pytest.approx(4 / 3)
    assert approved["time_multiplier"] == pytest.approx(1.5, rel=1e-3)
    assert approved["initial_improvement_bonus"] == pytest.approx(0.5, rel=1e-3)
    assert state["system_verdict"] == "approved"
    assert state["published_verdict"] == "approved"
    assert projected_at is not None


@pytest.mark.anyio
async def test_projector_does_not_inherit_bonus_from_previous_agent() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_agent_score(
            conn,
            hotkey="shared-hk",
            coldkey="shared-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_improvement_bonus=0.8,
        )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="shared-hk",
            coldkey="shared-ck",
            final_score=0.515,
            approved=False,
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)

    assert approved is not None
    assert approved["raw_improvement"] == pytest.approx(1)
    assert approved["time_multiplier"] == pytest.approx(1.5, rel=1e-3)
    assert approved["initial_improvement_bonus"] == pytest.approx(0.375, rel=1e-3)


@pytest.mark.anyio
async def test_projector_excludes_banned_approvals_from_elapsed_time() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_agent_score(
            conn,
            hotkey="eligible-leader-hk",
            coldkey="eligible-leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_improvement_bonus=0.25,
        )
        await _insert_agent_score(
            conn,
            hotkey="banned-improvement-hk",
            coldkey="banned-improvement-ck",
            final_score=0.51,
            approved=True,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.25,
        )
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test ban')",
            "banned-improvement-ck",
        )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.52,
            approved=False,
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)

    assert approved is not None
    assert approved["time_multiplier"] == pytest.approx(1.5, rel=1e-3)
    assert approved["initial_improvement_bonus"] == pytest.approx(0.5, rel=1e-3)


@pytest.mark.anyio
async def test_projector_excludes_review_rejected_approval_from_leader_and_elapsed_time() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        eligible_leader_id = await _insert_agent_score(
            conn,
            hotkey="eligible-leader-hk",
            coldkey="eligible-leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_improvement_bonus=0.25,
        )
        rejected_agent_id = await _insert_agent_score(
            conn,
            hotkey="rejected-hk",
            coldkey="rejected-ck",
            final_score=0.6,
            approved=True,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.25,
        )
        await _mark_review_rejected(conn, rejected_agent_id)
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.515,
            approved=False,
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)

    assert approved is not None
    assert approved["baseline_agent_id"] == eligible_leader_id
    assert approved["time_multiplier"] == pytest.approx(1.5, rel=1e-3)


@pytest.mark.anyio
@pytest.mark.parametrize("ineligible_kind", ["benchmark", "cancelled", "incomplete_validator"])
async def test_projector_excludes_non_reward_candidates_from_elapsed_time(ineligible_kind: str) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        eligible_leader_id = await _insert_agent_score(
            conn,
            hotkey="eligible-leader-hk",
            coldkey="eligible-leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=72),
            initial_improvement_bonus=0.25,
        )
        recent_agent_id = await _insert_agent_score(
            conn,
            hotkey=f"{ineligible_kind}-hk",
            coldkey=f"{ineligible_kind}-ck",
            final_score=0.6,
            approved=True,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.25,
            status="cancelled" if ineligible_kind == "cancelled" else "finished",
            validator_count=(
                config.NUM_EVALS_PER_AGENT - 1
                if ineligible_kind == "incomplete_validator"
                else config.NUM_EVALS_PER_AGENT
            ),
        )
        if ineligible_kind == "benchmark":
            await conn.execute(
                "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, 'test benchmark')",
                recent_agent_id,
            )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.515,
            approved=False,
        )
        await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)

    assert approved is not None
    assert approved["baseline_agent_id"] == eligible_leader_id
    assert approved["time_multiplier"] == pytest.approx(1.5, rel=1e-3)


@pytest.mark.anyio
async def test_projector_rechecks_leader_after_concurrent_ban() -> None:
    now = datetime.now(timezone.utc)
    leader_coldkey = "leader-ck"
    async with _db.pool.acquire() as conn:
        await _insert_agent_score(
            conn,
            hotkey="leader-hk",
            coldkey=leader_coldkey,
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.25,
        )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.49,
            approved=False,
        )
        await _insert_completed_approval_job(conn, candidate_id)

    async with _db.pool.acquire() as ban_conn:
        transaction = ban_conn.transaction()
        await transaction.start()
        await ban_conn.execute(
            "SELECT pg_advisory_xact_lock($1, hashtext($2))",
            COLDKEY_BAN_LOCK_NAMESPACE,
            leader_coldkey,
        )
        await ban_conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test ban')",
            leader_coldkey,
        )

        projection_task = asyncio.create_task(project_next_approval_job_state())
        for _ in range(100):
            if any("pg_advisory_xact_lock" in entry["query"] for entry in _db.DEBUG_QUERIES["running"]):
                break
            await asyncio.sleep(0.01)
        else:
            projection_task.cancel()
            raise AssertionError("Projection did not reach the leader coldkey ban lock")

        assert not projection_task.done()
        await transaction.commit()

    assert await projection_task is True
    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)

    assert approved is not None
    assert approved["baseline_agent_id"] is None


@pytest.mark.anyio
async def test_projector_rejects_stale_candidate_without_overwriting_judge_verdict() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_agent_score(
            conn,
            hotkey="leader-hk",
            coldkey="leader-ck",
            final_score=0.5,
            approved=True,
            approved_at=now - timedelta(hours=1),
            initial_improvement_bonus=0.25,
        )
        candidate_id = await _insert_agent_score(
            conn,
            hotkey="candidate-hk",
            coldkey="candidate-ck",
            final_score=0.51,
            approved=False,
        )
        job_id = await _insert_completed_approval_job(conn, candidate_id)

    assert await project_next_approval_job_state() is True

    async with _db.pool.acquire() as conn:
        approved = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", candidate_id)
        state = await conn.fetchrow("SELECT * FROM agent_approval_states WHERE agent_id = $1", candidate_id)
        job = await conn.fetchrow("SELECT aggregate_verdict, projected_at FROM approval_jobs WHERE job_id = $1", job_id)

    assert approved is None
    assert state["system_verdict"] == "rejected"
    assert state["published_verdict"] == "rejected"
    assert state["system_summary"] == "Candidate no longer meets the relative improvement threshold"
    assert job["aggregate_verdict"] == "approved"
    assert job["projected_at"] is not None
