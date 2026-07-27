from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import api.config as config
import queries.approval as approval_module
import utils.database as _db
from queries.approval import process_pending_disqualification_jobs, run_disqualification_reapproval
from queries.disqualification_job import count_pending_disqualification_jobs, enqueue_disqualification_job
from utils.incentives import calculate_time_multiplier

SET_ID = 71


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db, monkeypatch):
    monkeypatch.setattr(config, "INCENTIVE_START_SET_ID", SET_ID)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE disqualification_jobs, agent_approval_states, approval_jobs, approved_agents, "
            "agent_scores, disqualified_agents, benchmark_agent_ids, agents RESTART IDENTITY CASCADE"
        )
    yield


async def _insert_scored_agent(
    conn,
    *,
    hotkey,
    final_score,
    created_at,
    approved,
    approved_at=None,
    baseline_agent_id=None,
    system_verdict,
    projected_at=None,
) -> UUID:
    """Insert an agent that has reached an incentive decision.

    `created_at` is the agent's upload time. `projected_at` is the incentive DECISION time
    (defaults to created_at) — it drives replay chronology via a linked approval_jobs row.
    Pass distinct created_at / projected_at to test the case where upload order != decision order.
    """
    if projected_at is None:
        projected_at = created_at
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num,
                            status, created_at, ip_address)
        VALUES ($1, $2, $2, $2, 1, 'finished', $3, '127.0.0.1')
        """,
        agent_id,
        hotkey,
        created_at,
    )
    if approved:
        # approved_agents must be inserted BEFORE agent_scores: agent_scores is a derived table
        # (refresh_agent_scores_for_agent trigger rebuilds it from evaluations_hydrated whenever
        # approved_agents/agents change), so inserting approved_agents afterward would wipe the
        # manually-inserted agent_scores row.
        await conn.execute(
            """
            INSERT INTO approved_agents (agent_id, set_id, approved_at, baseline_agent_id,
                                         relative_improvement_units, time_multiplier, initial_reward_score)
            VALUES ($1, $2, $3, $4, 1, 1, 1)
            """,
            agent_id,
            SET_ID,
            approved_at,
            baseline_agent_id,
        )
    await conn.execute(
        """
        INSERT INTO agent_scores (agent_id, miner_hotkey, name, version_num, created_at, status,
                                  set_id, approved, approved_at, validator_count, final_score)
        VALUES ($1, $2, $2, 1, $3, 'finished', $4, $5, $6, $7, $8)
        """,
        agent_id,
        hotkey,
        created_at,
        SET_ID,
        approved,
        approved_at,
        config.NUM_EVALS_PER_AGENT,
        final_score,
    )
    # A completed+projected approval job carries the decision timestamp the replay orders on.
    job_id = uuid4()
    await conn.execute(
        """
        INSERT INTO approval_jobs (job_id, agent_id, set_id, status, policy_version,
                                   input_snapshot, aggregate_verdict, projected_at)
        VALUES ($1, $2, $3, 'completed', 'test', '{}'::jsonb, $4, $5)
        """,
        job_id,
        agent_id,
        SET_ID,
        system_verdict,
        projected_at,
    )
    await conn.execute(
        """
        INSERT INTO agent_approval_states (agent_id, set_id, latest_job_id, processing_status,
                                           system_verdict, updated_at)
        VALUES ($1, $2, $3, 'completed', $4, NOW())
        ON CONFLICT (agent_id, set_id) DO UPDATE
            SET latest_job_id = EXCLUDED.latest_job_id, system_verdict = EXCLUDED.system_verdict
        """,
        agent_id,
        SET_ID,
        job_id,
        system_verdict,
    )
    return agent_id


async def _is_approved(conn, agent_id) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM approved_agents WHERE agent_id = $1 AND set_id = $2", agent_id, SET_ID)
    return row is not None


async def _system_verdict(conn, agent_id) -> str:
    return await conn.fetchval(
        "SELECT system_verdict FROM agent_approval_states WHERE agent_id = $1 AND set_id = $2",
        agent_id,
        SET_ID,
    )


async def _disqualify(conn, agent_id) -> None:
    await conn.execute("INSERT INTO disqualified_agents (agent_id, reason) VALUES ($1, 'test')", agent_id)


@pytest.mark.anyio
async def test_case1_promote_b1_keep_b2_demote_c():
    # Threshold check (INCENTIVE_PERFORMANCE_THRESHOLD = 3%):
    #   B1=0.55 vs seeded leader A=0.50  -> +10%    -> qualifies (promoted)
    #   B2=0.55 vs new leader B1=0.55    -> 0%      -> rejects (stays rejected)
    #   C =0.56 vs leader B1=0.55        -> +1.82%  -> below 3% (demoted)
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        b1 = await _insert_scored_agent(
            conn,
            hotkey="B1",
            final_score=0.55,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        b2 = await _insert_scored_agent(
            conn,
            hotkey="B2",
            final_score=0.55,
            created_at=base + timedelta(hours=3),
            approved=False,
            system_verdict="rejected",
        )
        c = await _insert_scored_agent(
            conn,
            hotkey="C",
            final_score=0.56,
            created_at=base + timedelta(hours=4),
            approved=True,
            approved_at=base + timedelta(hours=4),
            baseline_agent_id=b,
            system_verdict="approved",
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True  # untouched, before B
        assert await _is_approved(conn, b) is False  # removed
        assert await _is_approved(conn, b1) is True  # promoted
        assert await _system_verdict(conn, b1) == "approved"
        assert await _is_approved(conn, b2) is False  # 0.55 vs 0.55 not an improvement
        assert await _system_verdict(conn, b2) == "rejected"
        assert await _is_approved(conn, c) is False  # demoted: 0.56 vs 0.55 < 3% threshold
        assert await _system_verdict(conn, c) == "rejected"


@pytest.mark.anyio
async def test_case2_promote_b1_keep_b2_no_c():
    # Same as Case 1 but without C downstream at all.
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        b1 = await _insert_scored_agent(
            conn,
            hotkey="B1",
            final_score=0.55,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        b2 = await _insert_scored_agent(
            conn,
            hotkey="B2",
            final_score=0.55,
            created_at=base + timedelta(hours=3),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True
        assert await _is_approved(conn, b) is False
        assert await _is_approved(conn, b1) is True
        assert await _system_verdict(conn, b1) == "approved"
        assert await _is_approved(conn, b2) is False
        assert await _system_verdict(conn, b2) == "rejected"


@pytest.mark.anyio
async def test_case3_c_demoted_against_b1_baseline():
    # Same shape as Case 1, but C's baseline_agent_id references B1 (the agent that will become
    # the new leader after B's removal) rather than B, to exercise the "C's original baseline
    # itself gets replaced downstream" path. Outcome (demote C) is identical: after replay the
    # leader when we reach C is B1 = 0.55, and C = 0.56 is +1.82%, below the 3% threshold.
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        b1 = await _insert_scored_agent(
            conn,
            hotkey="B1",
            final_score=0.55,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        c = await _insert_scored_agent(
            conn,
            hotkey="C",
            final_score=0.56,
            created_at=base + timedelta(hours=3),
            approved=True,
            approved_at=base + timedelta(hours=3),
            baseline_agent_id=b1,
            system_verdict="approved",
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True
        assert await _is_approved(conn, b) is False
        assert await _is_approved(conn, b1) is True  # promoted: 0.55 vs seeded leader A=0.50 -> +10%
        assert await _system_verdict(conn, b1) == "approved"
        assert await _is_approved(conn, c) is False  # demoted: 0.56 vs new leader B1=0.55 -> +1.82% < 3%
        assert await _system_verdict(conn, c) == "rejected"


@pytest.mark.anyio
async def test_first_approved_disqualified_new_baseline():
    # B was itself the first-approved agent for the set (baseline_agent_id=None). Disqualifying
    # it seeds current_leader=None, so the first downstream candidate that qualifies re-bootstraps
    # the competition and must get relative_improvement_units == 1.0 (calculate_relative_improvement's
    # "first approved agent" branch, triggered whenever leader_score is None).
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            baseline_agent_id=None,
            system_verdict="approved",
        )
        # D1's score must be >= B's (0.50) to survive the candidate query's floor filter.
        d1 = await _insert_scored_agent(
            conn,
            hotkey="D1",
            final_score=0.51,
            created_at=base + timedelta(hours=1),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, b) is False
        assert await _is_approved(conn, d1) is True
        assert await _system_verdict(conn, d1) == "approved"
        units = await conn.fetchval(
            "SELECT relative_improvement_units FROM approved_agents WHERE agent_id = $1 AND set_id = $2",
            d1,
            SET_ID,
        )
        assert units == pytest.approx(1.0)


@pytest.mark.anyio
async def test_b_never_approved_is_noop():
    # B reached a "rejected" incentive decision (never occupied approved_agents), so nothing
    # downstream was ever gated against it as leader. Disqualifying it must not touch anything.
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.40,
            created_at=base + timedelta(hours=1),
            approved=False,
            system_verdict="rejected",
        )
        d1 = await _insert_scored_agent(
            conn,
            hotkey="D1",
            final_score=0.51,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True
        assert await _system_verdict(conn, a) == "approved"
        assert await _is_approved(conn, d1) is False
        assert await _system_verdict(conn, d1) == "rejected"


@pytest.mark.anyio
async def test_orders_by_decision_time_not_upload_time():
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
            projected_at=base,
        )
        # X: uploaded AFTER B, but its incentive decision happened BEFORE B's.
        x = await _insert_scored_agent(
            conn,
            hotkey="X",
            final_score=0.90,
            created_at=base + timedelta(hours=5),  # late upload
            approved=False,
            system_verdict="rejected",
            projected_at=base + timedelta(hours=1),  # early decision
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=2),
            approved=True,
            approved_at=base + timedelta(hours=2),
            baseline_agent_id=a,
            system_verdict="approved",
            projected_at=base + timedelta(hours=2),
        )
        await _disqualify(conn, b)

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        # X decided before B → not in B's downstream set → untouched despite its high score/late upload.
        assert await _is_approved(conn, x) is False
        assert await _system_verdict(conn, x) == "rejected"


@pytest.mark.anyio
async def test_process_pending_runs_enqueued_job():
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        b1 = await _insert_scored_agent(
            conn,
            hotkey="B1",
            final_score=0.60,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)
        async with conn.transaction():
            await enqueue_disqualification_job(conn, agent_id=b, set_id=SET_ID)

    processed = await process_pending_disqualification_jobs()
    assert processed == 1
    assert await count_pending_disqualification_jobs() == 0

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, b) is False
        assert await _is_approved(conn, b1) is True  # 0.60 vs 0.50 = 20% > 3%


@pytest.mark.anyio
async def test_process_pending_stops_after_one_pass_on_permanent_failure(monkeypatch):
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        await _disqualify(conn, b)
        async with conn.transaction():
            await enqueue_disqualification_job(conn, agent_id=b, set_id=SET_ID)

    async def _always_raise(*, set_id, disqualified_agent_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(approval_module, "run_disqualification_reapproval", _always_raise)

    processed = await process_pending_disqualification_jobs()
    assert processed == 0

    assert await count_pending_disqualification_jobs() == 1
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT attempts, error, processed_at FROM disqualification_jobs WHERE agent_id = $1",
            b,
        )
    assert row is not None
    assert row["processed_at"] is None
    assert row["error"] is not None
    assert row["attempts"] <= 2

    first_attempts = row["attempts"]

    # A second invocation (e.g. a later startup) must retry the still-pending failing job.
    processed_again = await process_pending_disqualification_jobs()
    assert processed_again == 0

    async with _db.pool.acquire() as conn:
        row_after = await conn.fetchrow(
            "SELECT attempts FROM disqualification_jobs WHERE agent_id = $1",
            b,
        )
    assert row_after["attempts"] > first_attempts


@pytest.mark.anyio
async def test_process_pending_skips_failing_job_and_processes_healthy_one(monkeypatch):
    # A (older, permanently failing) must not starve B (newer, healthy) out of the same drain
    # invocation: the claim query must advance past A once it has been attempted.
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a_leader = await _insert_scored_agent(
            conn,
            hotkey="A-leader",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        failing_agent = await _insert_scored_agent(
            conn,
            hotkey="FAILING",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a_leader,
            system_verdict="approved",
        )
        healthy_agent = await _insert_scored_agent(
            conn,
            hotkey="HEALTHY",
            final_score=0.70,
            created_at=base + timedelta(hours=2),
            approved=True,
            approved_at=base + timedelta(hours=2),
            baseline_agent_id=a_leader,
            system_verdict="approved",
        )
        promotable = await _insert_scored_agent(
            conn,
            hotkey="PROMOTABLE",
            final_score=0.75,
            created_at=base + timedelta(hours=3),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, failing_agent)
        await _disqualify(conn, healthy_agent)
        async with conn.transaction():
            # Enqueue the failing job FIRST (older) so it sits at the head of the pending queue.
            await enqueue_disqualification_job(conn, agent_id=failing_agent, set_id=SET_ID)
        async with conn.transaction():
            await enqueue_disqualification_job(conn, agent_id=healthy_agent, set_id=SET_ID)

    real_run_disqualification_reapproval = run_disqualification_reapproval

    async def _fail_only_for_failing_agent(*, set_id, disqualified_agent_id):
        if disqualified_agent_id == failing_agent:
            raise RuntimeError("boom")
        return await real_run_disqualification_reapproval(set_id=set_id, disqualified_agent_id=disqualified_agent_id)

    monkeypatch.setattr(approval_module, "run_disqualification_reapproval", _fail_only_for_failing_agent)

    processed = await process_pending_disqualification_jobs()
    assert processed == 1

    async with _db.pool.acquire() as conn:
        failing_row = await conn.fetchrow(
            "SELECT processed_at, error FROM disqualification_jobs WHERE agent_id = $1",
            failing_agent,
        )
        healthy_row = await conn.fetchrow(
            "SELECT processed_at, error FROM disqualification_jobs WHERE agent_id = $1",
            healthy_agent,
        )
        assert await _is_approved(conn, promotable) is True  # proves healthy job's replay actually ran

    assert failing_row["processed_at"] is None
    assert failing_row["error"] is not None

    assert healthy_row is not None
    assert healthy_row["processed_at"] is not None  # B was NOT starved behind A


@pytest.mark.anyio
async def test_kept_leader_preserves_approved_at_for_time_multiplier():
    # Regression test for the bug where a kept already-approved leader lost its real
    # `approved_at` (rebuilt via a fresh AgentRankingProfile defaulting to None), flooring
    # `time_multiplier` to 1.0 for whatever gets promoted against it later.
    #
    # Chain: A (seed leader) -> B (disqualified) -> D (already-approved, still qualifies,
    # kept as the new leader with its REAL approved_at far in the past) -> E (rejected,
    # promoted against D).
    #
    # Threshold check (INCENTIVE_PERFORMANCE_THRESHOLD = 3%):
    #   D=0.55 vs seeded leader A=0.50  -> +10%    -> qualifies (kept as leader)
    #   E=0.60 vs new leader D=0.55     -> +9.09%  -> qualifies (promoted)
    base = datetime.now(timezone.utc) - timedelta(days=2)
    d_approved_at = datetime.now(timezone.utc) - timedelta(days=5)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        d = await _insert_scored_agent(
            conn,
            hotkey="D",
            final_score=0.55,
            created_at=base + timedelta(hours=2),
            approved=True,
            approved_at=d_approved_at,
            baseline_agent_id=b,
            system_verdict="approved",
        )
        e = await _insert_scored_agent(
            conn,
            hotkey="E",
            final_score=0.60,
            created_at=base + timedelta(hours=3),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)

    before = datetime.now(timezone.utc)
    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)
    after = datetime.now(timezone.utc)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True
        assert await _is_approved(conn, b) is False
        assert await _is_approved(conn, d) is True  # kept: still qualifies against A
        assert await _system_verdict(conn, d) == "approved"
        assert await _is_approved(conn, e) is True  # promoted against D
        assert await _system_verdict(conn, e) == "approved"

        e_row = await conn.fetchrow(
            "SELECT time_multiplier, baseline_agent_id FROM approved_agents WHERE agent_id = $1 AND set_id = $2",
            e,
            SET_ID,
        )
        assert e_row["baseline_agent_id"] == d

        # If the bug were present, current_leader for D would have approved_at=None, so
        # E's elapsed_hours would floor to 0.0 and time_multiplier would be exactly 1.0.
        floored_multiplier = calculate_time_multiplier(
            elapsed_hours=0.0,
            half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
            maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
        )
        assert floored_multiplier == pytest.approx(1.0)
        assert e_row["time_multiplier"] > floored_multiplier + 0.1

        # Sanity bound: elapsed_hours must reflect D's real approved_at (~5 days), not 0.
        min_elapsed_hours = (before - d_approved_at).total_seconds() / 3600
        max_elapsed_hours = (after - d_approved_at).total_seconds() / 3600
        expected_min = calculate_time_multiplier(
            elapsed_hours=min_elapsed_hours,
            half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
            maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
        )
        expected_max = calculate_time_multiplier(
            elapsed_hours=max_elapsed_hours,
            half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
            maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
        )
        assert expected_min - 1e-9 <= e_row["time_multiplier"] <= expected_max + 1e-9


@pytest.mark.anyio
async def test_surviving_approved_agent_snapshot_unchanged():
    # Spec test: byte-for-byte frozen-snapshot proof that a surviving (still-qualifying,
    # untouched) approved agent's approved_agents row is left completely alone by the replay,
    # not merely that it is still present (_is_approved only checks existence).
    base = datetime.now(timezone.utc) - timedelta(days=2)
    async with _db.pool.acquire() as conn:
        a = await _insert_scored_agent(
            conn,
            hotkey="A",
            final_score=0.50,
            created_at=base,
            approved=True,
            approved_at=base,
            system_verdict="approved",
        )
        b = await _insert_scored_agent(
            conn,
            hotkey="B",
            final_score=0.54,
            created_at=base + timedelta(hours=1),
            approved=True,
            approved_at=base + timedelta(hours=1),
            baseline_agent_id=a,
            system_verdict="approved",
        )
        b1 = await _insert_scored_agent(
            conn,
            hotkey="B1",
            final_score=0.55,
            created_at=base + timedelta(hours=2),
            approved=False,
            system_verdict="rejected",
        )
        await _disqualify(conn, b)

    async with _db.pool.acquire() as conn:
        # A is untouched by B's disqualification: it decided before B and never competes again.
        before_row = dict(
            await conn.fetchrow(
                """
                SELECT approved_at, baseline_agent_id, performance_delta, cost_delta,
                       relative_improvement_units, time_multiplier, initial_reward_score
                FROM approved_agents
                WHERE agent_id = $1 AND set_id = $2
                """,
                a,
                SET_ID,
            )
        )

    await run_disqualification_reapproval(set_id=SET_ID, disqualified_agent_id=b)

    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, a) is True
        after_row = dict(
            await conn.fetchrow(
                """
                SELECT approved_at, baseline_agent_id, performance_delta, cost_delta,
                       relative_improvement_units, time_multiplier, initial_reward_score
                FROM approved_agents
                WHERE agent_id = $1 AND set_id = $2
                """,
                a,
                SET_ID,
            )
        )

    assert after_row == before_row
    # b1 promoted as an independent sanity check that the replay actually ran.
    async with _db.pool.acquire() as conn:
        assert await _is_approved(conn, b1) is True
