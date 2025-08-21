from datetime import datetime, timezone
from typing import Optional, List, Tuple
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
async def get_agent_approved_banned(conn: asyncpg.Connection, version_id: str, miner_hotkey: str) -> Tuple[Optional[datetime], bool]:
    """Get approved and banned status from database"""
    approved_at = await conn.fetchval("""SELECT approved_at from approved_version_ids where version_id = $1""", version_id)
    banned = await conn.fetchval("""SELECT miner_hotkey from banned_hotkeys where miner_hotkey = $1""", miner_hotkey)
    return approved_at, banned is not None

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
async def get_ban_reason(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[str]:
    """Get the ban reason for a given miner hotkey"""
    return await conn.fetchval("""
        SELECT banned_reason FROM banned_hotkeys
        WHERE miner_hotkey = $1
    """, miner_hotkey)

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
async def ban_agents(conn: asyncpg.Connection, miner_hotkeys: List[str], reason: str):
    await conn.executemany("""
        INSERT INTO banned_hotkeys (miner_hotkey, banned_reason)
        VALUES ($1, $2)
    """, [(miner_hotkey, reason) for miner_hotkey in miner_hotkeys])

@db_transaction
async def approve_agent_version(conn: asyncpg.Connection, version_id: str, set_id: int, approved_at: Optional[datetime] = None):
    """
    Approve an agent version as a valid, non decoding agent solution
    Args:
        version_id: The agent version to approve
        set_id: The evaluation set ID
        approved_at: When the approval takes effect (defaults to now)
    """
    if approved_at is None:
        approved_at = datetime.now(timezone.utc)

    await conn.execute("""
        INSERT INTO approved_version_ids (version_id, set_id, approved_at)
        VALUES ($1, $2, $3)
    """, version_id, set_id, approved_at)
    
    # Update the top agents cache after approval (only if effective immediately)
    if approved_at <= datetime.now(timezone.utc):
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
            SELECT version_id FROM approved_version_ids WHERE approved_at <= NOW()
        )
        AND status = 'awaiting_screening'
    """)
    
    return [MinerAgent(**dict(result)) for result in results]

@db_operation
async def get_all_approved_version_ids(conn: asyncpg.Connection) -> List[str]:
    """
    Get all approved version IDs
    """
    data = await conn.fetch("SELECT version_id FROM approved_version_ids WHERE approved_at <= NOW()")
    return [str(row["version_id"]) for row in data]
