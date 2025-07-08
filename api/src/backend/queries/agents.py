from typing import Optional

import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent

@db_operation
async def store_agent(conn: asyncpg.Connection, agent: MinerAgent) -> bool:
    try:
        await conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, score)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (version_id) DO UPDATE 
            SET agent_name = EXCLUDED.agent_name,
                score = EXCLUDED.score,
                version_num = EXCLUDED.version_num
        """, agent.version_id, agent.miner_hotkey, agent.agent_name, agent.version_num, agent.created_at, agent.score)

        return True
    except:
        return False
    
@db_operation
async def get_agent_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, score "
        "FROM miner_agents WHERE miner_hotkey = $1",
        miner_hotkey
    )

    if not result:
        return None

    return MinerAgent(**dict(result))