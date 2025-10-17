import asyncpg
from utils.database import db_operation

@db_operation
async def get_top_scores_over_time(conn: asyncpg.Connection) -> list[dict]:
    query = """
        WITH
        max_set AS (
            SELECT MAX(set_id) as set_id FROM evaluation_sets
        ),
        time_series AS (
            SELECT
            generate_series(
                (
                SELECT
                    MIN(DATE_TRUNC('hour', agent_scores.created_at))
                FROM
                    agent_scores
                JOIN
                    agents a ON agent_scores.agent_id = a.agent_id
                WHERE
                    agent_scores.final_score IS NOT NULL
                    AND agent_scores.set_id = (SELECT set_id FROM max_set)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
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
                MAX(agent_scores.final_score)
            FROM
                agent_scores
            JOIN
                agents a ON agent_scores.agent_id = a.agent_id
            WHERE
                agent_scores.final_score IS NOT NULL
                AND agent_scores.created_at <= ts.hour
                AND agent_scores.set_id = (SELECT set_id FROM max_set)
                AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
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