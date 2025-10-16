from datetime import datetime, timezone
from typing import Optional, List, Tuple
import asyncpg

from utils.database import db_operation, db_transaction
from api.src.backend.entities import MinerAgent
from api.src.utils.models import TopAgentHotkey
import utils.logger as logger
from models.agent import AgentStatus, Agent


# TODO: ADAM - used in upload.py
@db_operation
async def get_latest_agent(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Agent]:
    result = await conn.fetchrow(
        "SELECT agent_id, miner_hotkey, name, version_num, created_at, status, ip_address "
        "FROM agents WHERE miner_hotkey = $1 ORDER BY version_num DESC LIMIT 1",
        miner_hotkey
    )

    if not result:
        return None

    return Agent(**dict(result))

# TODO: ADAM - used in src/endpoints/open_users.py
@db_operation
async def get_agent_by_agent_id(conn: asyncpg.Connection, agent_id: str) -> Optional[MinerAgent]:
    result = await conn.fetchrow(
        "SELECT agent_id, miner_hotkey, name, version_num, created_at, status "
        "FROM agents WHERE agent_id = $1",
        agent_id
    )

    if not result:
        return None

    return MinerAgent(**dict(result))

# USED IN agents.py -> FRONTEND OLD ENDPOINTS
# @db_operation
# async def get_agent_approved_banned(conn: asyncpg.Connection, agent_id: str, miner_hotkey: str) -> Tuple[Optional[datetime], bool]:
#     """Get approved and banned status from database"""
#     approved_at = await conn.fetchval("""SELECT approved_at from approved_agents where agent_id = $1""", agent_id)
#     banned = await conn.fetchval("""SELECT miner_hotkey from banned_hotkeys where miner_hotkey = $1""", miner_hotkey)
#     return approved_at, banned is not None

# TODO: ADAM - used in upload_agent_helpers.py
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

# TODO: ADAM - used in src/endpoints/upload.py
@db_operation
async def get_ban_reason(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[str]:
    """Get the ban reason for a given miner hotkey"""
    return await conn.fetchval("""
        SELECT banned_reason FROM banned_hotkeys
        WHERE miner_hotkey = $1
    """, miner_hotkey)

# USED IN scoring.py -> FRONTEND OLD ENDPOINTS
# @db_operation
# async def get_top_agent(conn: asyncpg.Connection) -> Optional[TopAgentHotkey]:
#     """
#     Gets the top approved agent using the agent_scores materialized view.
#     Uses only the maximum set_id and applies the 1.5% leadership rule.
#     """
#     from api.src.backend.entities import MinerAgentScored
    
#     logger.debug("Getting top agent using agent_scores materialized view")
#     return await MinerAgentScored.get_top_agent(conn)

# @db_operation
# async def get_agents_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> List[MinerAgent]:
#     result = await conn.fetch("""
#         SELECT agent_id, miner_hotkey, name, version_num, created_at, status
#         FROM agents
#         WHERE miner_hotkey = $1
#     """, miner_hotkey)
#     return [MinerAgent(**dict(result)) for result in result]

# USED in scoring.py -> ROUTE.TS FRONTEND
@db_transaction
async def ban_agents(conn: asyncpg.Connection, miner_hotkeys: List[str], reason: str):
    await conn.executemany("""
        INSERT INTO banned_hotkeys (miner_hotkey, banned_reason)
        VALUES ($1, $2)
    """, [(miner_hotkey, reason) for miner_hotkey in miner_hotkeys])

# USELESS - used in threshold_scheduler.py
# @db_transaction
# async def approve_agent_version(conn: asyncpg.Connection, agent_id: str, set_id: int, approved_at: Optional[datetime] = None):
#     """
#     Approve an agent version as a valid, non decoding agent solution
#     Args:
#         agent_id: The agent version to approve
#         set_id: The evaluation set ID
#         approved_at: When the approval takes effect (defaults to now)
#     """
#     if approved_at is None:
#         approved_at = datetime.now(timezone.utc)

#     await conn.execute("""
#         INSERT INTO approved_agents (agent_id, set_id, approved_at)
#         VALUES ($1, $2, $3)
#     """, agent_id, set_id, approved_at)
    
#     # Update the top agents cache after approval (only if effective immediately)
#     if approved_at <= datetime.now(timezone.utc):
#         try:
#             from api.src.utils.top_agents_manager import update_top_agents_cache
#             await update_top_agents_cache()
#             logger.info(f"Top agents cache updated after approving {agent_id}")
#         except Exception as e:
#             logger.error(f"Failed to update top agents cache after approval: {e}")
#             # Don't fail the approval if cache update fails

# @db_transaction
# async def set_approved_agents_to_awaiting_screening(conn: asyncpg.Connection) -> List[MinerAgent]:
#     """
#     Set all approved agent versions to awaiting_screening status for re-evaluation
#     Returns the list of agents that were updated
#     """
#     # Update approved agents to awaiting_screening status
    
#     # Get the updated agents
#     results = await conn.fetch(f"""
#         SELECT agent_id, miner_hotkey, name, version_num, created_at, status
#         FROM agents 
#         WHERE agent_id IN (
#             SELECT agent_id FROM approved_agents WHERE approved_at <= NOW()
#         )
#         AND status = '{AgentStatus.screening_1}'
#     """)
    
#     return [MinerAgent(**dict(result)) for result in results]

# @db_operation
# async def get_all_approved_agent_ids(conn: asyncpg.Connection) -> List[str]:
#     """
#     Get all approved version IDs
#     """
#     data = await conn.fetch("SELECT agent_id FROM approved_agents WHERE approved_at <= NOW()")
#     return [str(row["agent_id"]) for row in data]

