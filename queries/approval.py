import json
import logging
from datetime import datetime
from uuid import UUID, uuid4

import api.config as config
from models.agent import AgentStatus
from models.approval import (
    ApprovalEvaluationContext,
    ApprovalInputSnapshot,
    ApprovalPreScreeningContext,
    ApprovalProcessingStatus,
    ApprovalSourceReference,
    ApprovalValidatorScore,
    ApprovalVerdict,
)
from models.evaluation import EvaluationStatus
from models.evaluation_set import EvaluationSetGroup
from queries.banned_coldkey import get_banned_coldkey, lock_coldkey_ban_state
from queries.disqualification_job import (
    claim_next_pending_disqualification_job,
    mark_disqualification_job_processed,
    record_disqualification_job_error,
)
from queries.evaluation import (
    AgentRankingProfile,
    get_approved_leader_ranking_for_set,
    get_validator_agent_score_for_set,
)
from utils.database import DatabaseConnection, db_operation
from utils.incentives import (
    calculate_initial_reward_score,
    calculate_relative_improvement,
    calculate_time_multiplier,
)

logger = logging.getLogger(__name__)

INCENTIVE_APPROVAL_LOCK_NAMESPACE = -1730


def _pre_screening_verdict_from_job_status(job_status: str) -> str:
    """Map a pre_screening_jobs.status value to the verdict label exposed downstream."""

    if job_status == "succeeded":
        return "pass"
    if job_status == "failed":
        return "fail"
    return "needs_review"


@db_operation
async def enqueue_approval_job(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
    policy_version: str,
) -> UUID | None:
    """Create one active approval job and pending state row if none already exists."""

    async with conn.conn.transaction():
        snapshot = await _build_approval_input_snapshot(conn, agent_id=agent_id, set_id=set_id)
        return await _insert_approval_job_and_state(
            conn,
            agent_id=agent_id,
            set_id=set_id,
            policy_version=policy_version,
            snapshot=snapshot,
        )


@db_operation
async def finish_agent_and_enqueue_approval(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
    policy_version: str,
) -> bool:
    """Finish an agent and enqueue approval unless its coldkey is banned."""

    async with conn.conn.transaction():
        agent = await conn.fetchrow(
            """
            SELECT miner_coldkey
            FROM agents
            WHERE agent_id = $1
              AND status = $2
            FOR UPDATE
            """,
            agent_id,
            AgentStatus.evaluating.value,
        )
        if agent is None:
            return False

        miner_coldkey = agent["miner_coldkey"]
        if miner_coldkey is not None:
            await lock_coldkey_ban_state(conn, miner_coldkey)

        await conn.execute(
            "UPDATE agents SET status = $2 WHERE agent_id = $1",
            agent_id,
            AgentStatus.finished.value,
        )

        if miner_coldkey is not None and await get_banned_coldkey(miner_coldkey) is not None:
            return False

        snapshot = await _build_approval_input_snapshot(conn, agent_id=agent_id, set_id=set_id)
        await _insert_approval_job_and_state(
            conn,
            agent_id=agent_id,
            set_id=set_id,
            policy_version=policy_version,
            snapshot=snapshot,
        )

    return True


@db_operation
async def project_next_approval_job_state(conn: DatabaseConnection) -> bool:
    """Project one unprojected judge state into platform-owned approval tables."""

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT *
            FROM approval_jobs
            WHERE status IN ('needs_review', 'completed')
              AND projected_at IS NULL
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        if job is None:
            return False

        verdict = ApprovalVerdict(job["aggregate_verdict"]) if job["aggregate_verdict"] is not None else None
        effective_verdict = verdict
        effective_summary = job["aggregate_summary"]
        if job["status"] == "needs_review":
            processing_status = ApprovalProcessingStatus.needs_review.value
            published_verdict = None
            published_score = None
        else:
            processing_status = ApprovalProcessingStatus.completed.value
            published_verdict = verdict
            published_score = job["aggregate_score"]

        incentive_active = job["set_id"] >= config.INCENTIVE_START_SET_ID
        if job["status"] == "completed" and verdict == ApprovalVerdict.approved and incentive_active:
            rejection_reason = await _insert_incentive_approval(conn, job["agent_id"], job["set_id"])
            if rejection_reason is not None:
                effective_verdict = ApprovalVerdict.rejected
                published_verdict = ApprovalVerdict.rejected
                effective_summary = rejection_reason
                logger.info(
                    f"Rejecting approved judge result for agent_id={job['agent_id']} "
                    f"set_id={job['set_id']}: {rejection_reason}"
                )

        await _upsert_agent_approval_state(
            conn,
            agent_id=job["agent_id"],
            set_id=job["set_id"],
            latest_job_id=job["job_id"],
            processing_status=processing_status,
            system_verdict=effective_verdict,
            system_score=job["aggregate_score"],
            system_confidence=job["aggregate_confidence"],
            system_summary=effective_summary,
            published_verdict=published_verdict,
            published_score=published_score,
        )

        if job["status"] == "completed" and effective_verdict == ApprovalVerdict.approved and not incentive_active:
            await conn.execute(
                """
                INSERT INTO approved_agents (agent_id, set_id)
                VALUES ($1, $2)
                ON CONFLICT (agent_id, set_id) DO NOTHING
                """,
                job["agent_id"],
                job["set_id"],
            )

        await conn.execute(
            """
            UPDATE approval_jobs
            SET projected_at = NOW(), updated_at = NOW()
            WHERE job_id = $1
              AND projected_at IS NULL
            """,
            job["job_id"],
        )

    return True


async def _apply_incentive_decision(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
    candidate: AgentRankingProfile,
    leader: AgentRankingProfile | None,
    decision_time: datetime,
) -> str | None:
    """Decide and record one incentive approval against an explicit leader.

    Returns a rejection reason if the candidate does not qualify (nothing written),
    or None on success (approved_agents row inserted).
    """
    improvement = calculate_relative_improvement(
        candidate_score=candidate.final_score,
        candidate_cost=candidate.avg_cost_usd,
        leader_score=None if leader is None else leader.final_score,
        leader_cost=None if leader is None else leader.avg_cost_usd,
        performance_threshold=config.INCENTIVE_PERFORMANCE_THRESHOLD,
        cost_threshold=config.INCENTIVE_COST_THRESHOLD,
    )
    if not improvement.qualified:
        return "Candidate no longer meets the relative improvement threshold"

    last_competition_improvement = None if leader is None else leader.approved_at
    competition_elapsed_hours = _elapsed_hours(last_competition_improvement, decision_time)
    time_multiplier = calculate_time_multiplier(
        elapsed_hours=competition_elapsed_hours,
        half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
        maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
    )
    initial_reward_score = calculate_initial_reward_score(
        relative_improvement_units=improvement.relative_improvement_units,
        time_multiplier=time_multiplier,
    )

    await conn.execute(
        """
        INSERT INTO approved_agents (
            agent_id,
            set_id,
            approved_at,
            baseline_agent_id,
            performance_delta,
            cost_delta,
            relative_improvement_units,
            time_multiplier,
            initial_reward_score
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (agent_id, set_id) DO NOTHING
        """,
        agent_id,
        set_id,
        decision_time,
        None if leader is None else leader.agent_id,
        improvement.performance_delta,
        improvement.cost_delta,
        improvement.relative_improvement_units,
        time_multiplier,
        initial_reward_score,
    )
    return None


@db_operation
async def run_disqualification_reapproval(
    conn: DatabaseConnection,
    *,
    set_id: int,
    disqualified_agent_id: UUID,
) -> None:
    """Replay downstream incentive decisions after an agent is disqualified.

    Walks every agent whose incentive decision was made after the disqualified agent B's
    decision, in decision order, re-running the approve/reject gate against a leader lineage
    that no longer includes B.
    """

    async with conn.conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1, $2)",
            INCENTIVE_APPROVAL_LOCK_NAMESPACE,
            set_id,
        )

        # B's score and its decision moment. final_score comes from agent_scores; the decision
        # time is approval_jobs.projected_at of B's latest decision job (write-once, exists for
        # approved AND rejected agents). Do NOT use agent_scores.created_at (that is upload time).
        b_row = await conn.fetchrow(
            """
            SELECT ass.final_score, job.projected_at
            FROM agent_scores ass
            LEFT JOIN agent_approval_states st
                ON st.agent_id = ass.agent_id AND st.set_id = ass.set_id
            LEFT JOIN approval_jobs job
                ON job.job_id = st.latest_job_id
            WHERE ass.agent_id = $1 AND ass.set_id = $2
            """,
            disqualified_agent_id,
            set_id,
        )
        if b_row is None or b_row["projected_at"] is None:
            # B was never scored, or never reached an incentive decision: nothing downstream
            # was gated against it as leader.
            return

        b_approved = await conn.fetchrow(
            "SELECT baseline_agent_id FROM approved_agents WHERE agent_id = $1 AND set_id = $2",
            disqualified_agent_id,
            set_id,
        )
        if b_approved is None:
            # B never held an approved_agents row, so it never occupied the leader lineage:
            # nothing downstream was ever gated against it as leader. Nothing to replay.
            return
        baseline_agent_id = b_approved["baseline_agent_id"]

        await conn.execute(
            "DELETE FROM approved_agents WHERE agent_id = $1 AND set_id = $2",
            disqualified_agent_id,
            set_id,
        )

        current_leader = await _ranking_profile_for_agent(conn, baseline_agent_id, set_id)

        candidates = await conn.fetch(
            """
            SELECT
                ass.agent_id,
                ass.final_score,
                ass.created_at,
                ass.approved_at,
                job.projected_at,
                rt.avg_cost_usd,
                (aa.agent_id IS NOT NULL) AS is_approved
            FROM agent_scores ass
            INNER JOIN agents agent ON agent.agent_id = ass.agent_id
            INNER JOIN agent_approval_states st
                ON st.agent_id = ass.agent_id AND st.set_id = ass.set_id
            INNER JOIN approval_jobs job
                ON job.job_id = st.latest_job_id
            LEFT JOIN approved_agents aa
                ON aa.agent_id = ass.agent_id AND aa.set_id = ass.set_id
            LEFT JOIN LATERAL (
                SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
                FROM evaluations_hydrated eh
                WHERE eh.agent_id = ass.agent_id
                  AND eh.set_id = ass.set_id
                  AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
                  AND eh.status = 'success'::EvaluationStatus
            ) rt ON true
            WHERE ass.set_id = $1
              AND ass.agent_id <> $2
              AND ass.final_score >= $3
              AND job.projected_at > $4
              AND ass.validator_count = $5
              AND st.system_verdict IN ('approved', 'rejected')
              AND NOT EXISTS (
                  SELECT 1 FROM benchmark_agent_ids b WHERE b.agent_id = ass.agent_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM disqualified_agent_ids dq WHERE dq.agent_id = ass.agent_id
              )
            ORDER BY job.projected_at ASC, ass.agent_id ASC
            """,
            set_id,
            disqualified_agent_id,
            b_row["final_score"],
            b_row["projected_at"],
            config.NUM_EVALS_PER_AGENT,
        )

        decision_time: datetime = await conn.fetchval("SELECT NOW()")

        for row in candidates:
            candidate = AgentRankingProfile(
                final_score=row["final_score"],
                avg_cost_usd=row["avg_cost_usd"],
                created_at=row["created_at"],
                agent_id=row["agent_id"],
                approved_at=row["approved_at"],
            )
            if row["is_approved"]:
                improvement = calculate_relative_improvement(
                    candidate_score=candidate.final_score,
                    candidate_cost=candidate.avg_cost_usd,
                    leader_score=None if current_leader is None else current_leader.final_score,
                    leader_cost=None if current_leader is None else current_leader.avg_cost_usd,
                    performance_threshold=config.INCENTIVE_PERFORMANCE_THRESHOLD,
                    cost_threshold=config.INCENTIVE_COST_THRESHOLD,
                )
                if improvement.qualified:
                    last_leader_approved_at = None if current_leader is None else current_leader.approved_at
                    elapsed_hours = _elapsed_hours(last_leader_approved_at, decision_time)
                    time_multiplier = calculate_time_multiplier(
                        elapsed_hours=elapsed_hours,
                        half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
                        maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
                    )
                    initial_reward_score = calculate_initial_reward_score(
                        relative_improvement_units=improvement.relative_improvement_units,
                        time_multiplier=time_multiplier,
                    )
                    await conn.execute(
                        """
                        UPDATE approved_agents
                        SET baseline_agent_id = $3,
                            performance_delta = $4,
                            cost_delta = $5,
                            relative_improvement_units = $6,
                            time_multiplier = $7,
                            initial_reward_score = $8
                        WHERE agent_id = $1 AND set_id = $2
                        """,
                        row["agent_id"],
                        set_id,
                        None if current_leader is None else current_leader.agent_id,
                        improvement.performance_delta,
                        improvement.cost_delta,
                        improvement.relative_improvement_units,
                        time_multiplier,
                        initial_reward_score,
                    )
                    current_leader = candidate
                else:
                    await conn.execute(
                        "DELETE FROM approved_agents WHERE agent_id = $1 AND set_id = $2",
                        row["agent_id"],
                        set_id,
                    )
                    await _set_system_verdict(conn, row["agent_id"], set_id, "rejected")
            else:
                reason = await _apply_incentive_decision(
                    conn,
                    agent_id=row["agent_id"],
                    set_id=set_id,
                    candidate=candidate,
                    leader=current_leader,
                    decision_time=decision_time,
                )
                if reason is None:
                    await _set_system_verdict(conn, row["agent_id"], set_id, "approved")
                    current_leader = AgentRankingProfile(
                        final_score=candidate.final_score,
                        avg_cost_usd=candidate.avg_cost_usd,
                        created_at=candidate.created_at,
                        agent_id=candidate.agent_id,
                        approved_at=decision_time,
                    )


async def process_pending_disqualification_jobs() -> int:
    """Drain currently-pending disqualification jobs. Safe to call on a task or at startup.

    Not a @db_operation: each nested query acquires its own connection so a job's
    claim + replay + mark commit independently and no row lock is held across the replay.
    A failure on one job records an error and the drain continues with the next.

    Each invocation processes every DISTINCT currently-pending job at most once, then returns.
    A job that fails its replay is left pending (error recorded) for a LATER invocation to retry.
    The ids already attempted this invocation are passed to the claim query as an exclusion list,
    so the claim advances past a stuck failing job to the next distinct pending job instead of
    starving it forever behind the head-of-queue failure (claim orders by created_at, filtering
    processed_at IS NULL, so without exclusion it would keep re-returning the same failing job).
    """
    processed = 0
    attempted: list = []
    while True:
        job = await claim_next_pending_disqualification_job(attempted or None)
        if job is None:
            return processed
        attempted.append(job["id"])
        try:
            await run_disqualification_reapproval(
                set_id=job["set_id"],
                disqualified_agent_id=job["agent_id"],
            )
            await mark_disqualification_job_processed(job["id"])
            processed += 1
        except Exception as exc:  # noqa: BLE001 - one bad job must not wedge the drain
            logger.error(f"Disqualification reapproval job {job['id']} failed: {type(exc).__name__}: {exc}")
            await record_disqualification_job_error(job["id"], f"{type(exc).__name__}: {exc}")


async def _ranking_profile_for_agent(
    conn: DatabaseConnection, agent_id: UUID | None, set_id: int
) -> AgentRankingProfile | None:
    if agent_id is None:
        return None
    row = await conn.fetchrow(
        """
        SELECT ass.agent_id, ass.final_score, ass.approved_at, ass.created_at, rt.avg_cost_usd
        FROM agent_scores ass
        LEFT JOIN LATERAL (
            SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
            FROM evaluations_hydrated eh
            WHERE eh.agent_id = ass.agent_id AND eh.set_id = ass.set_id
              AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
              AND eh.status = 'success'::EvaluationStatus
        ) rt ON true
        WHERE ass.agent_id = $1 AND ass.set_id = $2
        """,
        agent_id,
        set_id,
    )
    if row is None:
        return None
    return AgentRankingProfile(
        final_score=row["final_score"],
        avg_cost_usd=row["avg_cost_usd"],
        created_at=row["created_at"],
        agent_id=row["agent_id"],
        approved_at=row["approved_at"],
    )


async def _set_system_verdict(conn: DatabaseConnection, agent_id: UUID, set_id: int, verdict: str) -> None:
    await conn.execute(
        """
        UPDATE agent_approval_states
        SET system_verdict = $3, updated_at = NOW()
        WHERE agent_id = $1 AND set_id = $2
        """,
        agent_id,
        set_id,
        verdict,
    )


async def _insert_incentive_approval(
    conn: DatabaseConnection,
    agent_id: UUID,
    set_id: int,
) -> str | None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1, $2)",
        INCENTIVE_APPROVAL_LOCK_NAMESPACE,
        set_id,
    )

    agent = await conn.fetchrow(
        """
        SELECT miner_coldkey, status
        FROM agents
        WHERE agent_id = $1
        FOR UPDATE
        """,
        agent_id,
    )
    if agent is None:
        return "Candidate no longer exists"

    if agent["status"] != AgentStatus.finished.value:
        return f"Candidate is not finished (status={agent['status']})"

    miner_coldkey = agent["miner_coldkey"]
    if miner_coldkey is not None:
        await lock_coldkey_ban_state(conn, miner_coldkey)
        if await get_banned_coldkey(miner_coldkey) is not None:
            return "Candidate coldkey is banned"

    candidate = await get_validator_agent_score_for_set(agent_id, set_id, config.NUM_EVALS_PER_AGENT)
    if candidate is None:
        return "Candidate no longer has a complete validator score"

    leader = await _get_ban_stable_leader(
        conn,
        set_id,
        agent_id,
    )

    decision_time: datetime = await conn.fetchval("SELECT NOW()")
    return await _apply_incentive_decision(
        conn,
        agent_id=agent_id,
        set_id=set_id,
        candidate=candidate,
        leader=leader,
        decision_time=decision_time,
    )


async def _get_ban_stable_leader(
    conn: DatabaseConnection,
    set_id: int,
    excluded_agent_id: UUID,
) -> AgentRankingProfile | None:
    """Return a leader whose coldkey ban state cannot change in this transaction."""

    while True:
        leader = await get_approved_leader_ranking_for_set(
            set_id,
            excluded_agent_id,
            config.NUM_EVALS_PER_AGENT,
        )
        if leader is None or leader.miner_coldkey is None:
            return leader

        await lock_coldkey_ban_state(conn, leader.miner_coldkey)
        current_leader = await get_approved_leader_ranking_for_set(
            set_id,
            excluded_agent_id,
            config.NUM_EVALS_PER_AGENT,
        )
        if current_leader is None or current_leader.agent_id == leader.agent_id:
            return current_leader


def _elapsed_hours(start: datetime | None, end: datetime) -> float:
    if start is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() / 3600)


async def _insert_approval_job_and_state(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
    policy_version: str,
    snapshot: ApprovalInputSnapshot,
) -> UUID | None:
    job_id = uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO approval_jobs (
            job_id,
            agent_id,
            set_id,
            policy_version,
            input_snapshot
        ) VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT DO NOTHING
        RETURNING job_id
        """,
        job_id,
        agent_id,
        set_id,
        policy_version,
        json.dumps(snapshot.model_dump(mode="json")),
    )
    if row is None:
        return None

    await conn.execute(
        """
        INSERT INTO agent_approval_states (
            agent_id,
            set_id,
            latest_job_id,
            processing_status,
            updated_at
        ) VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (agent_id, set_id) DO UPDATE
        SET
            latest_job_id = EXCLUDED.latest_job_id,
            processing_status = EXCLUDED.processing_status,
            updated_at = NOW()
        """,
        agent_id,
        set_id,
        job_id,
        ApprovalProcessingStatus.pending.value,
    )
    return job_id


async def _build_approval_input_snapshot(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
) -> ApprovalInputSnapshot:
    validator_rows = await conn.fetch(
        f"""
        SELECT validator_hotkey, score
        FROM evaluations_hydrated
        WHERE agent_id = $1
          AND set_id = $2
          AND status = '{EvaluationStatus.success.value}'
          AND evaluation_set_group = '{EvaluationSetGroup.validator.value}'::EvaluationSetGroup
        ORDER BY created_at ASC
        """,
        agent_id,
        set_id,
    )
    validator_scores = [
        ApprovalValidatorScore(validator_hotkey=row["validator_hotkey"], score=row["score"]) for row in validator_rows
    ]

    score_row = await conn.fetchrow(
        """
        SELECT final_score, validator_count
        FROM agent_scores
        WHERE agent_id = $1
          AND set_id = $2
        LIMIT 1
        """,
        agent_id,
        set_id,
    )
    computed_average = None
    if validator_scores:
        computed_average = sum(score.score for score in validator_scores) / len(validator_scores)

    pre_screening = None
    job_row = await conn.fetchrow(
        """
        SELECT job_id, status, reviewer_id
        FROM pre_screening_jobs
        WHERE agent_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        agent_id,
    )
    if job_row is not None and job_row["reviewer_id"] is not None:
        pre_screening = ApprovalPreScreeningContext(
            verdict=_pre_screening_verdict_from_job_status(job_row["status"]),
            resolution="human",
        )
    elif job_row is not None:
        result_row = await conn.fetchrow(
            """
            SELECT verdict, confidence, summary, policy_version
            FROM pre_screening_results
            WHERE job_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            job_row["job_id"],
        )
        if result_row is None:
            logger.warning(
                f"Pre-screening job {job_row['job_id']} has no result row; "
                f"omitting pre-screening context for agent {agent_id}"
            )
        else:
            pre_screening = ApprovalPreScreeningContext(
                verdict=_pre_screening_verdict_from_job_status(job_row["status"]),
                confidence=result_row["confidence"],
                summary=result_row["summary"],
                policy_version=result_row["policy_version"],
                resolution="auto",
            )

    return ApprovalInputSnapshot(
        agent_id=agent_id,
        set_id=set_id,
        source=ApprovalSourceReference(type="s3_key", key=f"{agent_id}/agent.py", file="agent.py"),
        evaluation_context=ApprovalEvaluationContext(
            final_validator_score=(
                score_row["final_score"]
                if score_row is not None and score_row["final_score"] is not None
                else computed_average
            ),
            validator_count=score_row["validator_count"] if score_row is not None else len(validator_scores),
            validator_scores=validator_scores,
            pre_screening=pre_screening,
        ),
    )


async def _upsert_agent_approval_state(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
    latest_job_id: UUID,
    processing_status: str,
    system_verdict: ApprovalVerdict | None,
    system_score: float | None,
    system_confidence: float | None,
    system_summary: str | None,
    published_verdict: ApprovalVerdict | None,
    published_score: float | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO agent_approval_states (
            agent_id,
            set_id,
            latest_job_id,
            processing_status,
            system_verdict,
            system_score,
            system_confidence,
            system_summary,
            published_verdict,
            published_score,
            updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
        ON CONFLICT (agent_id, set_id) DO UPDATE
        SET
            latest_job_id = EXCLUDED.latest_job_id,
            processing_status = EXCLUDED.processing_status,
            system_verdict = EXCLUDED.system_verdict,
            system_score = EXCLUDED.system_score,
            system_confidence = EXCLUDED.system_confidence,
            system_summary = EXCLUDED.system_summary,
            published_verdict = CASE
                WHEN agent_approval_states.published_verdict IS NULL
                 AND agent_approval_states.published_score IS NULL
                THEN EXCLUDED.published_verdict
                ELSE agent_approval_states.published_verdict
            END,
            published_score = CASE
                WHEN agent_approval_states.published_verdict IS NULL
                 AND agent_approval_states.published_score IS NULL
                THEN EXCLUDED.published_score
                ELSE agent_approval_states.published_score
            END,
            updated_at = NOW()
        """,
        agent_id,
        set_id,
        latest_job_id,
        processing_status,
        None if system_verdict is None else system_verdict.value,
        system_score,
        system_confidence,
        system_summary,
        None if published_verdict is None else published_verdict.value,
        published_score,
    )
