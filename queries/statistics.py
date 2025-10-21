from utils.database import db_operation, DatabaseConnection

@db_operation
async def get_inference_statistics():
    pass

@db_operation
async def top_score(conn: DatabaseConnection):
    return await conn.fetchval("SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)")

@db_operation
async def agents_created_24_hrs(conn: DatabaseConnection):
    return await conn.fetchval("SELECT COUNT(*) FROM agents WHERE created_at >= NOW() - INTERVAL '24 hours'")

@db_operation
async def score_improvement_24_hrs(conn: DatabaseConnection):
    return await conn.fetchval("SELECT COALESCE((SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)) - COALESCE((SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets) - 1), 0), 0)")

@db_operation
async def get_top_scores_over_last_week(conn: DatabaseConnection) -> list[dict]:
    # TODO: Hardcoded start time since it's slow to get the minimum date from the agent_scores view. We don't have indexes
    query = """
        WITH
        time_series AS (
            SELECT
            generate_series(
                '2025-10-15 22:00:00.000000 +00:00',
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