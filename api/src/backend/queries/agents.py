# Agent related queries
import asyncpg
from api.src.backend.db_manager import db_operation
from api.src.backend.entities import AgentVersion

@db_operation
async def create_agent(conn: asyncpg.Connection, agent: AgentVersion):
    await conn.execute("""
        INSERT INTO agents (miner_hotkey, name, latest_version, created_at, last_updated)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (miner_hotkey) DO UPDATE 
        SET name = EXCLUDED.name,
            latest_version = EXCLUDED.latest_version,
            last_updated = EXCLUDED.last_updated
    """, agent.miner_hotkey, agent.name, agent.latest_version, agent.created_at, agent.last_updated)

@db_operation
async def store_agent_version(conn: asyncpg.Connection, agent_version: AgentVersion):
    await conn.execute("""
        INSERT INTO agent_versions (version_id, miner_hotkey, version_num, created_at, score)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (version_id) DO UPDATE 
        SET score = EXCLUDED.score
    """, agent_version.version_id, agent_version.miner_hotkey, agent_version.version_num, agent_version.created_at, agent_version.score)
