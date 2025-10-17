from typing import Optional
import asyncpg

from utils.database import db_operation
from api.src.backend.entities import MinerAgent
from models.agent import Agent


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