from typing import Optional, Dict

from utils.database import db_operation, DatabaseConnection


@db_operation
async def get_weight_receiving_agent_hotkey(conn: DatabaseConnection) -> Optional[str]:
    # TODO ADAM: this query has artifacts of the old approval concept, fix
    current_leader = await conn.fetchrow(
        """
        SELECT 
            ass.miner_hotkey AS miner_hotkey
        FROM agent_scores ass
        WHERE 
            ass.approved 
            AND ass.approved_at <= NOW() 
            AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        ORDER BY ass.final_score DESC, ass.created_at ASC
        LIMIT 1
        """
    )
    if current_leader is None or "miner_hotkey" not in current_leader:
        return None
    return current_leader["miner_hotkey"]

async def get_weight_receiving_agent_info(conn: DatabaseConnection) -> Optional[Dict[str, str]]:
    current_leader = await conn.fetchrow(
        """
        SELECT 
            ass.miner_hotkey AS miner_hotkey,
            ass.agent_id AS agent_id
        FROM agent_scores ass
        WHERE 
            ass.approved 
            AND ass.approved_at <= NOW() 
            AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        ORDER BY ass.final_score DESC, ass.created_at ASC
        LIMIT 1
    """
    )
    if current_leader is None or "miner_hotkey" not in current_leader or "agent_id" not in current_leader:
        return None
    return current_leader