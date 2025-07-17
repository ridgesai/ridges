from typing import Optional, List

import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent
from api.src.utils.models import TopAgentHotkey
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

@db_operation
async def get_latest_agent(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status "
        "FROM miner_agents WHERE miner_hotkey = $1 ORDER BY version_num DESC LIMIT 1",
        miner_hotkey
    )

    if not result:
        return None

    return MinerAgent(**dict(result))

@db_operation
async def get_agent_by_version_id(conn: asyncpg.Connection, version_id: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status "
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
    where it's been scored by at least 2 validators and is in the approved versions list.
    Excludes banned miner hotkeys from consideration.
    Uses only evaluations from the maximum set_id.
    
    NEW RULE: Agents must beat the current leader by 1.5% to take over leadership.
    This prevents constant switching due to tiny improvements.
    """
    
    # First, get the maximum set_id
    max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    if not max_set_id_result or max_set_id_result['max_set_id'] is None:
        logger.warning("No evaluation sets found")
        return None
    
    max_set_id = max_set_id_result['max_set_id']
    
    # Get the current leader (highest scoring approved agent from max set_id)
    current_leader = await conn.fetchrow("""
        SELECT
            ma.miner_hotkey,
            e.version_id,
            AVG(e.score) AS avg_score
        FROM evaluations e
        JOIN approved_version_ids avi ON e.version_id = avi.version_id  -- Only approved versions
        JOIN miner_agents ma ON e.version_id = ma.version_id
        WHERE e.status = 'completed' 
          AND e.score IS NOT NULL
          AND e.score > 0  -- Exclude 0 scores
          AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
          AND e.set_id = $1  -- Only use max set_id
          AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        GROUP BY ma.miner_hotkey, e.version_id, ma.created_at
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
        ORDER BY AVG(e.score) DESC, ma.created_at ASC
        LIMIT 1
    """, max_set_id)
    
    if not current_leader:
        # No current leader - return highest scoring agent from max set_id
        fallback_agent = await conn.fetchrow("""
            SELECT
                ma.miner_hotkey,
                e.version_id,
                AVG(e.score) AS avg_score
            FROM evaluations e
            JOIN approved_version_ids avi ON e.version_id = avi.version_id
            JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE e.status = 'completed' 
              AND e.score IS NOT NULL
              AND e.score > 0  -- Exclude 0 scores
              AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
              AND e.set_id = $1  -- Only use max set_id
              AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            GROUP BY ma.miner_hotkey, e.version_id, ma.created_at
            HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
            ORDER BY AVG(e.score) DESC, ma.created_at ASC
            LIMIT 1
        """, max_set_id)
        
        if not fallback_agent:
            return None
            
        return TopAgentHotkey(
            miner_hotkey=fallback_agent['miner_hotkey'],
            version_id=str(fallback_agent['version_id']),
            avg_score=float(fallback_agent['avg_score'])
        )
    
    current_leader_score = current_leader['avg_score']
    required_score = current_leader_score * 1.015  # Must beat by 1.5%
    
    # Find agents that beat the current leader by 1.5% from max set_id
    challenger = await conn.fetchrow("""
        SELECT
            ma.miner_hotkey,
            e.version_id,
            AVG(e.score) AS avg_score
        FROM evaluations e
        JOIN approved_version_ids avi ON e.version_id = avi.version_id
        JOIN miner_agents ma ON e.version_id = ma.version_id
        WHERE e.status = 'completed' 
          AND e.score IS NOT NULL
          AND e.score > 0  -- Exclude 0 scores
          AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
          AND e.set_id = $2  -- Only use max set_id
          AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        GROUP BY ma.miner_hotkey, e.version_id, ma.created_at
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
          AND AVG(e.score) >= $1  -- Must beat current leader by 1.5%
        ORDER BY AVG(e.score) DESC, ma.created_at ASC
        LIMIT 1
    """, required_score, max_set_id)
    
    # Return challenger if found, otherwise keep current leader
    winner = challenger if challenger else current_leader
    
    return TopAgentHotkey(
        miner_hotkey=winner['miner_hotkey'],
        version_id=str(winner['version_id']),
        avg_score=float(winner['avg_score'])
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
    
    # Update the top agents cache after approval
    try:
        from api.src.utils.top_agents_manager import update_top_agents_cache
        await update_top_agents_cache()
        logger.info(f"Top agents cache updated after approving {version_id}")
    except Exception as e:
        logger.error(f"Failed to update top agents cache after approval: {e}")
        # Don't fail the approval if cache update fails

