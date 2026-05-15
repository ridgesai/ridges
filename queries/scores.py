from typing import Dict, Optional

from utils.database import DatabaseConnection, db_operation


@db_operation
async def get_weight_receiving_agent_hotkey(conn: DatabaseConnection) -> Optional[str]:
    # TODO ADAM: this query has artifacts of the old approval concept, fix
    current_leader = await conn.fetchrow(
        """
        SELECT 
            ass.miner_hotkey AS miner_hotkey
        FROM agent_scores ass
        LEFT JOIN LATERAL (
            SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
            FROM evaluations_hydrated eh
            WHERE eh.agent_id           = ass.agent_id
              AND eh.set_id             = ass.set_id
              AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
              AND eh.status             = 'success'::EvaluationStatus
        ) rt ON true
        WHERE
            ass.approved
            AND ass.approved_at <= NOW()
            AND ass.approved_at >= NOW() - INTERVAL '12 hours'
            AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND ass.status::text <> 'cancelled'
            AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        ORDER BY ass.final_score DESC, rt.avg_cost_usd ASC NULLS LAST, ass.created_at ASC
        LIMIT 1
        """
    )
    if current_leader is None or "miner_hotkey" not in current_leader:
        return None
    return current_leader["miner_hotkey"]


@db_operation
async def get_weight_receiving_agent_info(conn: DatabaseConnection) -> Optional[Dict[str, str]]:
    current_leader = await conn.fetchrow(
        """
        SELECT 
            ass.miner_hotkey AS miner_hotkey,
            ass.agent_id AS agent_id
        FROM agent_scores ass
        LEFT JOIN LATERAL (
            SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
            FROM evaluations_hydrated eh
            WHERE eh.agent_id           = ass.agent_id
              AND eh.set_id             = ass.set_id
              AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
              AND eh.status             = 'success'::EvaluationStatus
        ) rt ON true
        WHERE
            ass.approved
            AND ass.approved_at <= NOW()
            AND ass.approved_at >= NOW() - INTERVAL '12 hours'
            AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND ass.status::text <> 'cancelled'
            AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        ORDER BY ass.final_score DESC, rt.avg_cost_usd ASC NULLS LAST, ass.created_at ASC
        LIMIT 1
    """
    )
    if current_leader is None or "miner_hotkey" not in current_leader or "agent_id" not in current_leader:
        return None
    return current_leader
