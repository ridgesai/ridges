import json
from uuid import UUID, uuid4

from models.agent import AgentStatus
from models.approval import (
    ApprovalEvaluationContext,
    ApprovalInputSnapshot,
    ApprovalJob,
    ApprovalJudgeResponse,
    ApprovalPreScreeningContext,
    ApprovalProcessingStatus,
    ApprovalSourceReference,
    ApprovalValidatorScore,
    ApprovalVerdict,
)
from models.evaluation import EvaluationStatus
from models.evaluation_set import EvaluationSetGroup
from queries._row_parsing import parse_jsonb_fields
from utils.database import DatabaseConnection, db_operation


def _row_to_approval_job(row) -> ApprovalJob:
    return ApprovalJob(**parse_jsonb_fields(row, "input_snapshot"))


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
async def claim_next_approval_job(
    conn: DatabaseConnection,
    *,
    claimed_by: str,
    lease_seconds: int,
    max_attempts: int,
) -> ApprovalJob | None:
    """Claim the oldest available approval job, including expired leases."""

    claim_token = uuid4()
    async with conn.conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT *
            FROM approval_jobs
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
            UPDATE approval_jobs
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
            claimed_row["agent_id"],
            claimed_row["set_id"],
            claimed_row["job_id"],
            ApprovalProcessingStatus.running.value,
        )

    return _row_to_approval_job(claimed_row)


@db_operation
async def finalize_approval_job(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    claim_token: UUID,
    result: ApprovalJudgeResponse,
) -> bool:
    """Persist a completed approval result and update emissions gating if approved."""

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT *
            FROM approval_jobs
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

        await conn.executemany(
            """
            INSERT INTO approval_job_rounds (
                job_id,
                round_index,
                model,
                verdict,
                approval_score,
                confidence,
                summary,
                evidence,
                raw_response
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            """,
            [
                (
                    job_id,
                    round_result.round_index,
                    round_result.model,
                    round_result.verdict.value,
                    round_result.approval_score,
                    round_result.confidence,
                    round_result.summary,
                    json.dumps([evidence.model_dump(mode="json") for evidence in round_result.evidence]),
                    json.dumps(round_result.raw_response),
                )
                for round_result in result.rounds
            ],
        )

        await conn.execute(
            """
            UPDATE approval_jobs
            SET
                status = 'completed',
                claim_token = NULL,
                claimed_at = NULL,
                claimed_by = NULL,
                lease_expires_at = NULL,
                last_error = NULL,
                source_sha256 = $3,
                aggregate_verdict = $4,
                aggregate_score = $5,
                aggregate_confidence = $6,
                aggregate_summary = $7,
                updated_at = NOW()
            WHERE job_id = $1
            """,
            job_id,
            claim_token,
            result.source_sha256,
            result.aggregate_verdict.value,
            result.aggregate_score,
            result.aggregate_confidence,
            result.aggregate_summary,
        )

        await _upsert_agent_approval_state(
            conn,
            agent_id=job["agent_id"],
            set_id=job["set_id"],
            latest_job_id=job_id,
            processing_status=ApprovalProcessingStatus.completed.value,
            system_verdict=result.aggregate_verdict,
            system_score=result.aggregate_score,
            system_confidence=result.aggregate_confidence,
            system_summary=result.aggregate_summary,
            published_verdict=result.aggregate_verdict,
            published_score=result.aggregate_score,
        )

        if result.aggregate_verdict == ApprovalVerdict.approved:
            await conn.execute(
                """
                INSERT INTO approved_agents (agent_id, set_id)
                VALUES ($1, $2)
                ON CONFLICT (agent_id, set_id) DO NOTHING
                """,
                job["agent_id"],
                job["set_id"],
            )

    return True


@db_operation
async def record_approval_job_error(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    claim_token: UUID,
    error_message: str,
    backoff_seconds: int,
    max_attempts: int,
) -> bool:
    """Record a retryable approval job failure or finalize as needs review."""

    async with conn.conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT *
            FROM approval_jobs
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
            await _complete_job_as_needs_review(
                conn,
                job_id=job_id,
                agent_id=job["agent_id"],
                set_id=job["set_id"],
                error_message=error_message,
            )
            return True

        await conn.execute(
            """
            UPDATE approval_jobs
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
            job["agent_id"],
            job["set_id"],
            job_id,
            ApprovalProcessingStatus.error.value,
        )

    return True


@db_operation
async def move_exhausted_approval_jobs_to_review(
    conn: DatabaseConnection,
    *,
    max_attempts: int,
) -> int:
    """Finalize exhausted jobs that can no longer be retried."""

    moved = 0
    async with conn.conn.transaction():
        rows = await conn.fetch(
            """
            SELECT *
            FROM approval_jobs
            WHERE status IN ('error', 'running')
              AND attempt_count >= $1
              AND (status = 'error' OR lease_expires_at < NOW())
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            """,
            max_attempts,
        )
        for row in rows:
            await _complete_job_as_needs_review(
                conn,
                job_id=row["job_id"],
                agent_id=row["agent_id"],
                set_id=row["set_id"],
                error_message=row["last_error"] or "Automatic approval judge attempts exhausted",
            )
            moved += 1

    return moved


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

    pre_screening_row = await conn.fetchrow(
        """
        SELECT verdict, confidence, summary, policy_version
        FROM pre_screening_results
        WHERE agent_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        agent_id,
    )
    pre_screening = None
    if pre_screening_row is not None:
        pre_screening = ApprovalPreScreeningContext(
            verdict=pre_screening_row["verdict"],
            confidence=pre_screening_row["confidence"],
            summary=pre_screening_row["summary"],
            policy_version=pre_screening_row["policy_version"],
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


async def _complete_job_as_needs_review(
    conn: DatabaseConnection,
    *,
    job_id: UUID,
    agent_id: UUID,
    set_id: int,
    error_message: str,
) -> None:
    summary = "Automatic approval judge attempts exhausted; manual review required."
    await conn.execute(
        """
        UPDATE approval_jobs
        SET
            status = 'completed',
            claim_token = NULL,
            claimed_at = NULL,
            claimed_by = NULL,
            lease_expires_at = NULL,
            last_error = $2,
            aggregate_verdict = $3,
            aggregate_score = NULL,
            aggregate_confidence = NULL,
            aggregate_summary = $4,
            updated_at = NOW()
        WHERE job_id = $1
        """,
        job_id,
        error_message,
        ApprovalVerdict.needs_review.value,
        summary,
    )

    await _upsert_agent_approval_state(
        conn,
        agent_id=agent_id,
        set_id=set_id,
        latest_job_id=job_id,
        processing_status=ApprovalProcessingStatus.completed.value,
        system_verdict=ApprovalVerdict.needs_review,
        system_score=None,
        system_confidence=None,
        system_summary=summary,
        published_verdict=ApprovalVerdict.needs_review,
        published_score=None,
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
