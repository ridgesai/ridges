from typing import Optional, Any

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
async def get_top_agent(conn: asyncpg.Connection) -> dict[str, Any]:
    """
    Gets the top approved agents miner hotkey and version id from the database,
    where its been scored by at least 1 validator and is in the approved versions list.
    Excludes banned miner hotkeys from consideration.
    Returns None if no approved versions exist.
    """
    top_agent = conn.fetchrow("""
        WITH approved_version_scores AS (               -- 1.  score + validator count for APPROVED versions only
            SELECT
                e.version_id,
                AVG(e.score)                       AS avg_score,
                COUNT(DISTINCT e.validator_hotkey) AS validator_cnt
            FROM evaluations e
            JOIN approved_version_ids av_approved ON e.version_id = av_approved.version_id  -- ONLY approved versions
            WHERE e.status = 'completed'
            AND e.score  IS NOT NULL
            GROUP BY e.version_id
            HAVING COUNT(DISTINCT e.validator_hotkey) >= 1
        ),

        top_approved_score AS (                         -- 2.  the absolute best score among approved versions
            SELECT MAX(avg_score) AS max_score
            FROM approved_version_scores
        ),

        close_enough_approved AS (                      -- 3.  approved scores ≥ 98% of the best approved
            SELECT
                avs.version_id,
                avs.avg_score,
                av.created_at,
                ROW_NUMBER() OVER (ORDER BY av.created_at ASC) AS rn  -- oldest first
            FROM approved_version_scores avs
            JOIN agent_versions av ON av.version_id = avs.version_id
            CROSS JOIN top_approved_score tas
            WHERE avs.avg_score >= tas.max_score * 0.98    -- within 2%
        )

        SELECT
            a.miner_hotkey,
            ce.version_id,
            ce.avg_score
        FROM close_enough_approved   ce
        JOIN agent_versions av ON av.version_id = ce.version_id
        JOIN agents         a  ON a.miner_hotkey    = av.miner_hotkey
        WHERE ce.rn = 1;
    """)

    if top_agent is None:
        return None

    return {
        "miner_hotkey": top_agent[0],
        "version_id": top_agent[1],
        "avg_score": top_agent[2]
    }

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
    """, version_id)