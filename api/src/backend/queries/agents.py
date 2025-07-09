from typing import Optional, Any

import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent
from api.src.utils.models import TopAgentHotkey

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
async def get_latest_agent(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, score "
        "FROM miner_agents WHERE miner_hotkey = $1 ORDER BY version_num DESC LIMIT 1",
        miner_hotkey
    )

    if not result:
        return None

    return MinerAgent(**dict(result))

@db_operation
async def get_agent_by_version_id(conn: asyncpg.Connection, version_id: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, score "
        "FROM miner_agents WHERE version_id = $1",
        version_id
    )

    if not result:
        return None

    return MinerAgent(**dict(result))

@db_operation
async def check_if_agent_banned(conn: asyncpg.Connection, miner_hotkey: str) -> bool:
    exists = await conn.fetchval("""
    SELECT EXISTS(
        SELECT 1 FROM banned_hotkeys
        WHERE miner_hotkey = $1
    );
    """, miner_hotkey)

    if exists:
        return True
    
    return False

@db_operation
async def get_top_agent(conn: asyncpg.Connection) -> Optional[TopAgentHotkey]:
    """
    Gets the top approved agent's miner hotkey and version id from the database,
    where it's been scored by at least 1 validator and is in the approved versions list.
    Excludes banned miner hotkeys from consideration.
    Returns the agent with the highest score - ANY improvement makes you the leader.
    """
    top_agent = await conn.fetchrow("""
        SELECT
            ma.miner_hotkey,
            e.version_id,
            AVG(e.score) AS avg_score
        FROM evaluations e
        JOIN approved_version_ids avi ON e.version_id = avi.version_id  -- Only approved versions
        JOIN miner_agents ma ON ma.version_id = e.version_id
        WHERE e.status = 'completed'
        AND e.score IS NOT NULL
        AND ma.miner_hotkey NOT IN (
            SELECT miner_hotkey FROM banned_hotkeys
        )
        GROUP BY ma.miner_hotkey, e.version_id, ma.created_at
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 1  -- At least 1 validator evaluation
        ORDER BY AVG(e.score) DESC, ma.created_at ASC  -- Highest score wins, oldest breaks ties
        LIMIT 1;
    """)
    
    if not top_agent:
        return None
    
    return TopAgentHotkey(
        miner_hotkey=top_agent['miner_hotkey'],
        version_id=str(top_agent['version_id']),
        avg_score=float(top_agent['avg_score'])
    )

@db_operation
async def ban_agent(conn: asyncpg.Connection, miner_hotkey: str):
    await conn.execute("""
        INSERT INTO banned_hotkeys (miner_hotkey)
        VALUES ($1)
    """, miner_hotkey)

@db_operation
async def approve_agent_version(conn: asyncpg.Connection, version_id: str):
    """
    Approve an agent version as a valid, non decoding agent solution
    """
    await conn.execute("""
        INSERT INTO approved_version_ids (version_id)
        VALUES ($1)
        ON CONFLICT (version_id) DO NOTHING
    """, version_id)