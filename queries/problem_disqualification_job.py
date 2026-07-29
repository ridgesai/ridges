from uuid import UUID

from asyncpg import Record

from models.evaluation_set import EvaluationSetGroup
from utils.database import DatabaseConnection, db_operation


def _group_value(set_group: EvaluationSetGroup | str) -> str:
    return set_group.value if isinstance(set_group, EvaluationSetGroup) else set_group


async def enqueue_problem_disqualification_job(
    conn: DatabaseConnection,
    *,
    set_id: int,
    set_group: EvaluationSetGroup | str,
    problem_name: str,
) -> UUID | None:
    """Insert a pending job. Returns None if one is already pending for this problem.

    Called inside the caller's transaction (the disqualify endpoint), so no @db_operation.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO problem_disqualification_jobs (set_id, set_group, problem_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (set_id, set_group, problem_name) WHERE processed_at IS NULL DO NOTHING
        RETURNING id
        """,
        set_id,
        _group_value(set_group),
        problem_name,
    )
    return row["id"] if row is not None else None


@db_operation
async def claim_next_pending_problem_disqualification_job(
    conn: DatabaseConnection, exclude_ids: list[UUID] | None = None
) -> Record | None:
    """Claim the oldest pending job, excluding ids already attempted this invocation."""
    return await conn.fetchrow(
        """
        UPDATE problem_disqualification_jobs
        SET attempts = attempts + 1
        WHERE id = (
            SELECT id
            FROM problem_disqualification_jobs
            WHERE processed_at IS NULL
              AND ($1::uuid[] IS NULL OR id <> ALL($1::uuid[]))
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, set_id, set_group, problem_name
        """,
        exclude_ids,
    )


@db_operation
async def mark_problem_disqualification_job_processed(conn: DatabaseConnection, id: UUID) -> None:
    await conn.execute(
        "UPDATE problem_disqualification_jobs SET processed_at = NOW(), error = NULL WHERE id = $1",
        id,
    )


@db_operation
async def record_problem_disqualification_job_error(conn: DatabaseConnection, id: UUID, error: str) -> None:
    await conn.execute(
        "UPDATE problem_disqualification_jobs SET error = $2 WHERE id = $1",
        id,
        error,
    )


@db_operation
async def count_pending_problem_disqualification_jobs(conn: DatabaseConnection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM problem_disqualification_jobs WHERE processed_at IS NULL")
