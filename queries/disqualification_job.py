from uuid import UUID

from asyncpg import Record

from utils.database import DatabaseConnection, db_operation


async def enqueue_disqualification_job(
    conn: DatabaseConnection,
    *,
    agent_id: UUID,
    set_id: int,
) -> UUID | None:
    """Insert a pending disqualification job. Returns None if one is already pending for this agent.

    Called inside the caller's transaction (e.g. the disqualify endpoint), so no @db_operation.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO disqualification_jobs (agent_id, set_id)
        VALUES ($1, $2)
        ON CONFLICT (agent_id) WHERE processed_at IS NULL DO NOTHING
        RETURNING id
        """,
        agent_id,
        set_id,
    )
    return row["id"] if row is not None else None


@db_operation
async def claim_next_pending_disqualification_job(
    conn: DatabaseConnection, exclude_ids: list[UUID] | None = None
) -> Record | None:
    """Claim the oldest pending job, optionally excluding ids already attempted this invocation.

    The exclusion lets a single drain invocation skip past a job it already attempted (and that
    failed, leaving it pending) so it advances to the next distinct pending job instead of being
    starved behind it. See process_pending_disqualification_jobs in queries/approval.py.
    """
    return await conn.fetchrow(
        """
        UPDATE disqualification_jobs
        SET attempts = attempts + 1
        WHERE id = (
            SELECT id
            FROM disqualification_jobs
            WHERE processed_at IS NULL
              AND ($1::uuid[] IS NULL OR id <> ALL($1::uuid[]))
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, agent_id, set_id
        """,
        exclude_ids,
    )


@db_operation
async def mark_disqualification_job_processed(conn: DatabaseConnection, id: UUID) -> None:
    await conn.execute(
        "UPDATE disqualification_jobs SET processed_at = NOW(), error = NULL WHERE id = $1",
        id,
    )


@db_operation
async def record_disqualification_job_error(conn: DatabaseConnection, id: UUID, error: str) -> None:
    await conn.execute(
        "UPDATE disqualification_jobs SET error = $2 WHERE id = $1",
        id,
        error,
    )


@db_operation
async def count_pending_disqualification_jobs(conn: DatabaseConnection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM disqualification_jobs WHERE processed_at IS NULL")
