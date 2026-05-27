import json
from datetime import datetime, timezone
from uuid import UUID

import utils.logger as logger
from models.agent import AgentStatus
from models.pre_screening_judge import PreScreeningResultPayload, PreScreeningVerdict
from utils.database import DatabaseConnection, db_operation


async def insert_pending_pre_screening_job(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    policy_version: str,
) -> None:
    """Insert a pending pre-screening job for the judge worker to pick up."""

    await conn.execute(
        """
        INSERT INTO pre_screening_jobs (agent_id, policy_version)
        VALUES ($1, $2)
        """,
        agent_id,
        policy_version,
    )


async def insert_terminal_pre_screening_job_with_result(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    policy_version: str,
    job_status: str,
    result: PreScreeningResultPayload,
) -> None:
    """Insert a pre-screening job already in terminal status alongside its synthetic result.

    Used for the duplicate-source short-circuit at upload time, where the platform short-circuits
    a pre-screening verdict without going through the judge.
    """

    row = await conn.fetchrow(
        """
        INSERT INTO pre_screening_jobs (
            agent_id,
            policy_version,
            status,
            next_attempt_at
        ) VALUES ($1, $2, $3, NOW())
        RETURNING job_id
        """,
        agent_id,
        policy_version,
        job_status,
    )
    if row is None:
        return

    await _insert_pre_screening_result(
        conn,
        job_id=row["job_id"],
        agent_id=agent_id,
        result=result,
    )


@db_operation
async def project_next_pre_screening_job_state(conn: DatabaseConnection) -> bool:
    """Mirror one terminal pre-screening job into agents.status, marking it projected.

    Always marks projected_at even if the agent transition is a no-op (e.g. some other process
    has already moved the agent off pre_screening). Otherwise the same row would churn forever.
    """

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT job_id, agent_id, status
            FROM pre_screening_jobs
            WHERE status IN ('succeeded', 'failed', 'needs_review')
              AND projected_at IS NULL
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        if job is None:
            return False

        agent_status = _agent_status_for_job_terminal_status(job["status"])

        update_result = await conn.execute(
            """
            UPDATE agents
            SET status = $2
            WHERE agent_id = $1
              AND status IN ($3, $4)
            """,
            job["agent_id"],
            agent_status.value,
            AgentStatus.pre_screening.value,
            AgentStatus.pre_screening_needs_review.value,
        )
        if update_result == "UPDATE 0":
            logger.warning(
                f"Pre-screening projector left agent {job['agent_id']} status untouched for "
                f"job {job['job_id']}: agent was not in pre_screening or pre_screening_needs_review."
            )

        await conn.execute(
            """
            UPDATE pre_screening_jobs
            SET projected_at = NOW(), updated_at = NOW()
            WHERE job_id = $1
            """,
            job["job_id"],
        )

    return True


def duplicate_source_result(*, policy_version: str, matched_agent_id: UUID) -> PreScreeningResultPayload:
    """Build the synthetic fail result used when an agent's source matches another in the current set."""

    now = datetime.now(timezone.utc).isoformat()
    raw_response = {
        "generated_by": "platform",
        "reason": "duplicate_source",
        "matched_agent_id": str(matched_agent_id),
        "created_at": now,
    }
    return PreScreeningResultPayload(
        verdict=PreScreeningVerdict.fail,
        confidence=1.0,
        summary=f"Duplicate source. Identical SHA-256 to agent {matched_agent_id} already in this set.",
        categories=["duplicate_source"],
        evidence=[],
        static_findings=["duplicate_source"],
        model=None,
        fallback_used=False,
        policy_version=policy_version,
        raw_response=raw_response,
        error_message=None,
    )


async def _insert_pre_screening_result(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    agent_id: UUID,
    result: PreScreeningResultPayload,
) -> None:
    """Insert the durable judge result payload for audit and debugging."""

    await conn.execute(
        """
        INSERT INTO pre_screening_results (
            job_id,
            agent_id,
            verdict,
            confidence,
            summary,
            categories,
            evidence,
            static_findings,
            model,
            fallback_used,
            policy_version,
            raw_response,
            error_message
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, $9, $10, $11, $12::jsonb, $13)
        """,
        job_id,
        agent_id,
        result.verdict.value,
        result.confidence,
        result.summary,
        json.dumps(result.categories),
        json.dumps(result.evidence),
        json.dumps(result.static_findings),
        result.model,
        result.fallback_used,
        result.policy_version,
        json.dumps(result.raw_response),
        result.error_message,
    )


def _agent_status_for_job_terminal_status(job_status: str) -> AgentStatus:
    """Map a terminal pre_screening_jobs.status value to the agent status to project."""

    if job_status == "succeeded":
        return AgentStatus.screening_1
    if job_status == "failed":
        return AgentStatus.failed_pre_screening
    if job_status == "needs_review":
        return AgentStatus.pre_screening_needs_review
    raise ValueError(f"Unexpected pre-screening job terminal status: {job_status}")
