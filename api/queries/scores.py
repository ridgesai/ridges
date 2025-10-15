from typing import Optional

import asyncpg

from utils.database import db_operation


@db_operation
async def get_weight_receiving_agent_hotkey(conn: asyncpg.Connection) -> Optional[str]:
    """
    Gets current top agent, who has been approved and that does not have a banned hotkey
    """
    current_leader = await conn.fetchrow(
        """
        SELECT 
            ass.miner_hotkey AS miner_hotkey
        FROM agent_scores ass
        WHERE 
            ass.approved 
            AND ass.approved_at <= NOW() 
            AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
        ORDER BY ass.final_score DESC, ass.created_at ASC
        LIMIT 1
        """
    )
    if current_leader is None or "miner_hotkey" not in current_leader:
        return None
    return current_leader["miner_hotkey"]

@db_operation
async def get_treasury_hotkey(conn: asyncpg.Connection) -> Optional[str]:
    treasury_hotkey_data = await conn.fetchrow(
        """
        SELECT hotkey FROM treasury_wallets WHERE active = TRUE ORDER BY created_at DESC LIMIT 1
        """
    )
    if not treasury_hotkey_data or "hotkey" not in treasury_hotkey_data:
        return None
    return treasury_hotkey_data["hotkey"]
