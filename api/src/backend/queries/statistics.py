from typing import Any
from datetime import datetime

from pydantic import BaseModel
from uuid import UUID
import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent, Inference

@db_operation
async def get_24_hour_statistics(conn: asyncpg.Connection) -> dict[str, Any]:
    """
    Get 24-hour statistics for miner agents including count, recent iterations, 
    top score, and daily improvement metrics
    """
    result = await conn.fetchrow("""
        SELECT
            COUNT(*) as number_of_agents,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as agent_iterations_last_24_hours,
            MAX(score) as top_agent_score,
            MAX(score) - COALESCE(
                (SELECT MAX(ma.score)
                FROM miner_agents ma
                JOIN approved_version_ids av ON ma.version_id = av.version_id
                WHERE ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                ), 0
            ) as daily_score_improvement
        FROM miner_agents
        WHERE miner_hotkey NOT IN (
            SELECT miner_hotkey
            FROM banned_hotkeys
        );
    """)
    
    if result is None:
        return {
            'number_of_agents': 0,
            'agent_iterations_last_24_hours': 0,
            'top_agent_score': None,
            'daily_score_improvement': 0
        }
    
    return {
        'number_of_agents': result['number_of_agents'],
        'agent_iterations_last_24_hours': result['agent_iterations_last_24_hours'],
        'top_agent_score': result['top_agent_score'],
        'daily_score_improvement': result['daily_score_improvement']
    }

class RunningEvaluation(BaseModel):
    version_id: UUID
    validator_hotkey: str
    started_at: datetime
    agent_name: str
    miner_hotkey: str
    version_num: int

@db_operation
async def get_currently_running_evaluations(conn: asyncpg.Connection) -> list[RunningEvaluation]:
    results = await conn.fetch("""
        select e.version_id, e.validator_hotkey, e.started_at, a.agent_name, a.miner_hotkey, a.version_num
        from evaluations e
        left join miner_agents a on a.version_id = e.version_id
        where e.status = 'running';
    """)

    return [RunningEvaluation(**dict(row)) for row in results]

@db_operation
async def get_top_agents(conn: asyncpg.Connection, num_agents: int = 3) -> list[MinerAgent]:
    results = await conn.fetch("""
        SELECT
            version_id,
            miner_hotkey,
            agent_name,
            version_num,
            created_at,
            score,
            status
        FROM miner_agents
        WHERE score IS NOT NULL
        AND miner_hotkey NOT IN (
            SELECT miner_hotkey
            FROM banned_hotkeys
            )
        AND score > 0
        ORDER BY score DESC
        LIMIT $1;
    """, num_agents)

    return [MinerAgent(**dict(row)) for row in results]

@db_operation
async def get_agent_summary_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> list[MinerAgent]:
    results = await conn.fetch("""
        select
            version_id,
            miner_hotkey,
            agent_name,
            version_num,
            created_at,
            score,
            status
        from miner_agents where miner_hotkey = $1 order by created_at desc;
    """, miner_hotkey)

    
    return [MinerAgent(**dict(row)) for row in results]

class QueuePositionPerValidator(BaseModel): 
    validator_hotkey: str
    queue_position: int

@db_operation
async def get_queue_position_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> list[QueuePositionPerValidator]:
    results = await conn.fetch("""
        WITH latest_version AS (
            SELECT version_id
            FROM   miner_agents
            WHERE  miner_hotkey = $1
            ORDER  BY version_num DESC
            LIMIT  1
        ),

        waiting_queue AS (
            SELECT
                e.evaluation_id,
                e.validator_hotkey,
                ROW_NUMBER() OVER (                    --    ranked per validator
                    PARTITION BY e.validator_hotkey
                    ORDER BY     e.created_at
                ) AS queue_position
            FROM   evaluations   e
            JOIN   miner_agents  ma ON ma.version_id = e.version_id
            WHERE  e.status = 'waiting'                -- only items still in the queue
            AND  ma.miner_hotkey NOT IN (SELECT miner_hotkey
                                        FROM banned_hotkeys)  -- skip banned miners
        )

        SELECT
            w.validator_hotkey,
            w.queue_position
        FROM   waiting_queue w
        JOIN   evaluations  e  ON e.evaluation_id = w.evaluation_id
        JOIN   latest_version lv ON lv.version_id  = e.version_id
        ORDER  BY w.validator_hotkey;
    """, miner_hotkey)

    return [QueuePositionPerValidator(**dict(row)) for row in results]

@db_operation
async def get_inference_details_for_run(conn: asyncpg.Connection, run_id: str) -> list[Inference]:
    runs = await conn.fetch("""
        select 
            id, run_id, messages->0->>'content' as message, temperature, model,cost, response, total_tokens, created_at, finished_at 
        from inferences 
        where run_id = $1;
    """, run_id)

    return [Inference(**dict(run)) for run in runs]