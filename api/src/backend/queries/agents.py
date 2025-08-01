from typing import Optional, List

import asyncpg

from api.src.backend.db_manager import db_operation, db_transaction
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
    Gets the top approved agent using the agent_scores materialized view.
    Uses only the maximum set_id and applies the 1.5% leadership rule.
    """
    from api.src.backend.entities import MinerAgentScored
    
    logger.debug("Getting top agent using agent_scores materialized view")
    return await MinerAgentScored.get_top_agent(conn)

@db_operation
async def get_agents_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> List[MinerAgent]:
    result = await conn.fetch("""
        SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status
        FROM miner_agents
        WHERE miner_hotkey = $1
    """, miner_hotkey)
    return [MinerAgent(**dict(result)) for result in result]

@db_transaction
async def ban_agent(conn: asyncpg.Connection, miner_hotkey: str):
    await conn.execute("""
        INSERT INTO banned_hotkeys (miner_hotkey)
        VALUES ($1)
    """, miner_hotkey)

@db_transaction
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

@db_transaction
async def set_approved_agents_to_awaiting_screening(conn: asyncpg.Connection) -> List[MinerAgent]:
    """
    Set all approved agent versions to awaiting_screening status for re-evaluation
    Returns the list of agents that were updated
    """
    # Update approved agents to awaiting_screening status
    
    # Get the updated agents
    results = await conn.fetch("""
        SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status
        FROM miner_agents 
        WHERE version_id IN (
            SELECT version_id FROM approved_version_ids
        )
        AND status = 'awaiting_screening'
    """)
    
    return [MinerAgent(**dict(result)) for result in results]
