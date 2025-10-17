import asyncpg
from utils.database import db_operation

@db_operation
async def get_top_scores_over_last_week(conn: asyncpg.Connection) -> list[dict]:
    query = """
        WITH
        time_series AS (
            SELECT
            generate_series(
                (
                SELECT
                    MIN(DATE_TRUNC('hour', created_at))
                FROM
                    agent_scores
                WHERE
                    final_score IS NOT NULL
                ),
                DATE_TRUNC('hour', NOW()),
                '1 hour'::interval
            ) as hour
        )
        SELECT
        ts.hour,
        COALESCE(
            (
            SELECT
                MAX(final_score)
            FROM
                agent_scores
            WHERE
                final_score IS NOT NULL
                AND created_at <= ts.hour
            ),
            0
        ) as top_score
        FROM
        time_series ts
        ORDER BY
        ts.hour
    """
    rows = await conn.fetch(query)
    return [dict(row) for row in rows]