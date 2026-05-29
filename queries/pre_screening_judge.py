import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from models.agent import AgentStatus
from models.pre_screening_judge import PreScreeningJob, PreScreeningResultPayload, PreScreeningVerdict
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)


def _row_to_pre_screening_job(row) -> PreScreeningJob:
    return PreScreeningJob(**dict(row))


@db_operation
async def claim_next_pre_screening_job(
    conn: DatabaseConnection,
    *,
    claimed_by: str,
    lease_seconds: int,
    max_attempts: int,
) -> PreScreeningJob | None:
    """Claim the oldest available job, including expired leases and retryable errors."""

    claim_token = uuid4()
    async with conn.conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT *
            FROM pre_screening_jobs
            WHERE (
                    (
                        status IN ('pending', 'error')
                        AND next_attempt_at <= NOW()
                    )
                    OR (
                        status = 'running'
                        AND lease_expires_at < NOW()
                    )
                  )
              AND attempt_count < $1
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            max_attempts,
        )
        if row is None:
            return None

        claimed_row = await conn.fetchrow(
            """
            UPDATE pre_screening_jobs
            SET
                status = 'running',
                attempt_count = attempt_count + 1,
                claim_token = $2,
                claimed_at = NOW(),
                claimed_by = $3,
                lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE job_id = $1
            RETURNING *
            """,
            row["job_id"],
            claim_token,
            claimed_by,
            lease_seconds,
        )

    return _row_to_pre_screening_job(claimed_row)


@db_operation
async def finalize_pre_screening_job(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    claim_token: UUID,
    result: PreScreeningResultPayload,
) -> bool:
    """Store the judge result and move the agent to the status implied by the verdict."""

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT *
            FROM pre_screening_jobs
            WHERE job_id = $1
              AND status = 'running'
              AND claim_token = $2
            FOR UPDATE
            """,
            job_id,
            claim_token,
        )
        if job is None:
            return False

        job_status, agent_status = _statuses_for_verdict(result.verdict)
        await _insert_pre_screening_result(conn, job_id=job_id, agent_id=job["agent_id"], result=result)

        await conn.execute(
            """
            UPDATE pre_screening_jobs
            SET
                status = $2,
                claim_token = NULL,
                claimed_at = NULL,
                claimed_by = NULL,
                lease_expires_at = NULL,
                last_error = $3,
                updated_at = NOW()
            WHERE job_id = $1
            """,
            job_id,
            job_status,
            result.error_message,
        )

        update_result = await conn.execute(
            """
            UPDATE agents
            SET status = $2
            WHERE agent_id = $1
              AND status = $3
            """,
            job["agent_id"],
            agent_status.value,
            AgentStatus.pre_screening.value,
        )
        _warn_if_agent_status_not_updated(
            update_result,
            agent_id=job["agent_id"],
            job_id=job_id,
            attempted_status=agent_status,
        )

    return True


@db_operation
async def record_pre_screening_job_error(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    claim_token: UUID,
    error_message: str,
    backoff_seconds: int,
    max_attempts: int,
    policy_version: str,
) -> bool:
    """Record a failed judge attempt, backing off or sending the agent to review."""

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT *
            FROM pre_screening_jobs
            WHERE job_id = $1
              AND status = 'running'
              AND claim_token = $2
            FOR UPDATE
            """,
            job_id,
            claim_token,
        )
        if job is None:
            return False

        if job["attempt_count"] >= max_attempts:
            result = _max_attempts_result(error_message=error_message, policy_version=policy_version)
            await _insert_pre_screening_result(conn, job_id=job_id, agent_id=job["agent_id"], result=result)
            await conn.execute(
                """
                UPDATE pre_screening_jobs
                SET
                    status = 'needs_review',
                    claim_token = NULL,
                    claimed_at = NULL,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    last_error = $2,
                    updated_at = NOW()
                WHERE job_id = $1
                """,
                job_id,
                error_message,
            )
            update_result = await conn.execute(
                """
                UPDATE agents
                SET status = $2
                WHERE agent_id = $1
                  AND status = $3
                """,
                job["agent_id"],
                AgentStatus.pre_screening_needs_review.value,
                AgentStatus.pre_screening.value,
            )
            _warn_if_agent_status_not_updated(
                update_result,
                agent_id=job["agent_id"],
                job_id=job_id,
                attempted_status=AgentStatus.pre_screening_needs_review,
            )
            return True

        await conn.execute(
            """
            UPDATE pre_screening_jobs
            SET
                status = 'error',
                claim_token = NULL,
                claimed_at = NULL,
                claimed_by = NULL,
                lease_expires_at = NULL,
                next_attempt_at = NOW() + (($2 * attempt_count) * INTERVAL '1 second'),
                last_error = $3,
                updated_at = NOW()
            WHERE job_id = $1
            """,
            job_id,
            backoff_seconds,
            error_message,
        )

    return True


@db_operation
async def move_exhausted_pre_screening_jobs_to_review(
    conn: DatabaseConnection,
    *,
    max_attempts: int,
) -> int:
    """Move expired or errored jobs to review once they are out of retries."""

    moved = 0
    async with conn.conn.transaction():
        rows = await conn.fetch(
            """
            SELECT *
            FROM pre_screening_jobs
            WHERE status IN ('error', 'running')
              AND attempt_count >= $1
              AND (status = 'error' OR lease_expires_at < NOW())
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            """,
            max_attempts,
        )
        for row in rows:
            result = _max_attempts_result(
                error_message=row["last_error"] or "Pre-screening judge attempts exhausted",
                policy_version=row["policy_version"],
            )
            await _insert_pre_screening_result(conn, job_id=row["job_id"], agent_id=row["agent_id"], result=result)
            await conn.execute(
                """
                UPDATE pre_screening_jobs
                SET
                    status = 'needs_review',
                    claim_token = NULL,
                    claimed_at = NULL,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE job_id = $1
                """,
                row["job_id"],
            )
            update_result = await conn.execute(
                """
                UPDATE agents
                SET status = $2
                WHERE agent_id = $1
                  AND status = $3
                """,
                row["agent_id"],
                AgentStatus.pre_screening_needs_review.value,
                AgentStatus.pre_screening.value,
            )
            _warn_if_agent_status_not_updated(
                update_result,
                agent_id=row["agent_id"],
                job_id=row["job_id"],
                attempted_status=AgentStatus.pre_screening_needs_review,
            )
            moved += 1

    return moved


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
            source_sha256,
            raw_response,
            error_message
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, $9, $10, $11, $12, $13::jsonb, $14)
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
        result.source_sha256,
        json.dumps(result.raw_response),
        result.error_message,
    )


def _statuses_for_verdict(verdict: PreScreeningVerdict) -> tuple[str, AgentStatus]:
    """Map a judge verdict to job and agent statuses."""

    if verdict == PreScreeningVerdict.pass_:
        return "succeeded", AgentStatus.screening_1
    if verdict == PreScreeningVerdict.fail:
        return "failed", AgentStatus.failed_pre_screening
    return "needs_review", AgentStatus.pre_screening_needs_review


def _warn_if_agent_status_not_updated(
    update_result: str,
    *,
    agent_id: UUID,
    job_id: UUID,
    attempted_status: AgentStatus,
) -> None:
    if update_result == "UPDATE 0":
        logger.warning(
            f"Pre-screening judge job {job_id} moved to terminal state, but agent {agent_id} was not moved to "
            f"{attempted_status.value} because it was no longer in {AgentStatus.pre_screening.value}."
        )


def _max_attempts_result(*, error_message: str, policy_version: str) -> PreScreeningResultPayload:
    """Build the synthetic result used when the judge cannot produce a verdict."""

    now = datetime.now(timezone.utc).isoformat()
    raw_response = {
        "generated_by": "platform",
        "reason": "max_attempts_exhausted",
        "created_at": now,
    }
    return PreScreeningResultPayload(
        verdict=PreScreeningVerdict.needs_review,
        confidence=0.0,
        summary="Pre-screening judge attempts exhausted; manual review required.",
        categories=["max_attempts_exhausted", "judge_unavailable"],
        evidence=[],
        static_findings=[],
        model=None,
        fallback_used=False,
        policy_version=policy_version,
        source_sha256=None,
        raw_response=raw_response,
        error_message=error_message,
    )
