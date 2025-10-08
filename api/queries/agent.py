import asyncpg

from typing import Optional, Final
from api.src.backend.db_manager import db_operation
from uuid import UUID

MIN_EVALS: Final[int] = 3


@db_operation
async def get_next_agent_id_awaiting_evaluation_for_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[UUID]:
    if validator_hotkey.startswith("screener-1"):
        result = await conn.fetchrow("""
            SELECT agent_id FROM screener_1_queue LIMIT 1
        """)
    elif validator_hotkey.startswith("screener-2"):
        result = await conn.fetchrow("""
             SELECT agent_id FROM screener_2_queue LIMIT 1
        """)
    else:
        result = await conn.fetchrow(
            """
            WITH
                validator_eval_counts AS (
                    SELECT
                        agent_id,
                        COUNT(*) AS num_evals,
                        BOOL_OR(validator_hotkey = $1) AS already_evaluated
                    FROM evaluations_hydrated
                    WHERE evaluations_hydrated.status = 'success'
                      AND validator_hotkey NOT LIKE 'screener-%'
                    GROUP BY agent_id
                ),
                screener_2_scores AS (
                    SELECT agent_id, MAX(score) AS score FROM evaluations_hydrated
                    WHERE validator_hotkey LIKE 'screener-2%'
                      AND evaluations_hydrated.status = 'success'
                    GROUP BY agent_id
                )
            SELECT
                agent_id
            FROM agents
                 INNER JOIN screener_2_scores USING (agent_id)
                 LEFT JOIN validator_eval_counts USING (agent_id)
            WHERE
                agents.status = 'evaluating'
                AND NOT COALESCE(already_evaluated, false)
                AND NOT COALESCE(num_evals, 0) < $2
            ORDER BY
                screener_2_scores.score DESC,
                agents.created_at ASC
            LIMIT 1
            """,
            validator_hotkey,
            MIN_EVALS
        )
        return UUID(result["agent_id"])