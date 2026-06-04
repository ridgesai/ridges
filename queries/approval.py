import json
import logging
from uuid import UUID, uuid4

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
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)


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
    """Atomically move an agent to finished and enqueue its approval job."""

    async with conn.conn.transaction():
        update_result = await conn.execute(
            """
            UPDATE agents
            SET status = $2
            WHERE agent_id = $1
              AND status = $3
            """,
            agent_id,
            AgentStatus.finished.value,
            AgentStatus.evaluating.value,
        )
        if update_result == "UPDATE 0":
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
        if job["status"] == "needs_review":
            processing_status = ApprovalProcessingStatus.needs_review.value
            published_verdict = None
            published_score = None
        else:
            processing_status = ApprovalProcessingStatus.completed.value
            published_verdict = verdict
            published_score = job["aggregate_score"]

        await _upsert_agent_approval_state(
            conn,
            agent_id=job["agent_id"],
            set_id=job["set_id"],
            latest_job_id=job["job_id"],
            processing_status=processing_status,
            system_verdict=verdict,
            system_score=job["aggregate_score"],
            system_confidence=job["aggregate_confidence"],
            system_summary=job["aggregate_summary"],
            published_verdict=published_verdict,
            published_score=published_score,
        )

        if job["status"] == "completed" and verdict == ApprovalVerdict.approved:
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
