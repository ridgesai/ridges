from typing import Optional, List
import asyncpg
from api.src.utils.models import AgentVersionDetails
from pydantic import BaseModel, Field

class Agent(BaseModel):
    pass

async def get_agent_by_id(conn: asyncpg.Connection, agent_id: str) -> Optional[dict]:
    """Get agent by ID"""
    result = await conn.fetch(
        "SELECT agent_id, miner_hotkey, name, latest_version, created_at, last_updated "
        "FROM agents WHERE agent_id = $1",
        agent_id
    )
    return dict(result[0]) if result else None