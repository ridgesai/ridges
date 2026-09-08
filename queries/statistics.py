from typing import Dict, Optional

from models.evaluation_set import EvaluationSetGroup
from utils.database import DatabaseConnection, db_operation


# NOTE: None is returned if there are no successful evaluations for a given
#       evaluation set group.
@db_operation
async def get_average_score_per_evaluation_set_group(
    conn: DatabaseConnection,
    set_id: int,
) -> Dict[EvaluationSetGroup, Optional[float]]:
    rows = await conn.fetch(
        """
        SELECT
            eh.evaluation_set_group as validator_type,
            AVG(eh.score) as average_score
        FROM evaluations_hydrated eh
            JOIN agents a on a.agent_id = eh.agent_id 
        WHERE eh.status = 'success'
            AND eh.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = eh.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND eh.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND eh.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        GROUP BY validator_type
        """,
        set_id,
    )

    result = {EvaluationSetGroup(row["validator_type"]): float(row["average_score"]) for row in rows}

    if EvaluationSetGroup.screener_1 not in result:
        result[EvaluationSetGroup.screener_1] = None
    if EvaluationSetGroup.screener_2 not in result:
        result[EvaluationSetGroup.screener_2] = None
    if EvaluationSetGroup.validator not in result:
        result[EvaluationSetGroup.validator] = None

    return result


# NOTE: None is returned if there are no successful evaluations for a given
#       evaluation set group.
@db_operation
async def get_average_wait_time_per_evaluation_set_group(
    conn: DatabaseConnection,
    set_id: int,
    required_validator_count: int | None,
) -> Dict[EvaluationSetGroup, Optional[float]]:
    result = {}

    result[EvaluationSetGroup.screener_1] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (e.finished_at - a.created_at))) AS average_wait_time
        FROM evaluations_hydrated e
            JOIN agents a ON e.agent_id = a.agent_id
        WHERE e.status = 'success'
            AND e.evaluation_set_group = '{EvaluationSetGroup.screener_1.value}'::EvaluationSetGroup
            AND e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = e.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
    )

    result[EvaluationSetGroup.screener_2] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (sc2_e.finished_at - sc1_e.finished_at))) AS average_wait_time
        FROM evaluations_hydrated sc1_e
            JOIN evaluations_hydrated sc2_e ON sc1_e.agent_id = sc2_e.agent_id
            JOIN agents a ON sc1_e.agent_id = a.agent_id
        WHERE sc1_e.status = 'success' AND sc2_e.status = 'success'
            AND sc1_e.evaluation_set_group = '{EvaluationSetGroup.screener_1.value}'::EvaluationSetGroup
            AND sc2_e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}'::EvaluationSetGroup
            AND sc1_e.set_id = $1
            AND sc2_e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = sc1_e.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND sc2_e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
    )

    result[EvaluationSetGroup.validator] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (v_e.finished_at - sc2_e.finished_at))) AS average_wait_time
        FROM evaluations_hydrated sc2_e
            JOIN (
                SELECT
                    v_e2.agent_id,
                    MAX(v_e2.finished_at) AS finished_at,
                    COUNT(DISTINCT v_e2.validator_hotkey) AS validator_count
                    FROM evaluations_hydrated v_e2
                    WHERE v_e2.status = 'success'
                    AND v_e2.evaluation_set_group = '{EvaluationSetGroup.validator.value}'::EvaluationSetGroup
                    AND v_e2.set_id = $1
                GROUP BY v_e2.agent_id
            ) v_e ON sc2_e.agent_id = v_e.agent_id
            JOIN agents a ON sc2_e.agent_id = a.agent_id
        WHERE sc2_e.status = 'success'
            AND sc2_e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}'::EvaluationSetGroup
            AND sc2_e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = sc2_e.set_id)
            AND v_e.validator_count = $2
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND v_e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
        required_validator_count,
    )

    return result
