from typing import Any
from datetime import datetime

from pydantic import BaseModel
from uuid import UUID
import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent, Inference, MinerAgentWithScores

@db_operation
async def get_24_hour_statistics(conn: asyncpg.Connection) -> dict[str, Any]:
    """
    Get 24-hour statistics for miner agents including count, recent iterations, 
    top score, and daily improvement metrics
    """
    # Get current statistics based on computed scores from max set_id
    max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    max_set_id = max_set_id_result['max_set_id'] if max_set_id_result else None
    
    if max_set_id is None:
        # No evaluation sets exist yet
        total_agents = await conn.fetchval("SELECT COUNT(*) FROM miner_agents")
        recent_agents = await conn.fetchval("SELECT COUNT(*) FROM miner_agents WHERE created_at >= NOW() - INTERVAL '24 hours'")
        
        result = {
            'number_of_agents': total_agents or 0,
            'agent_iterations_last_24_hours': recent_agents or 0,
            'top_agent_score': None,
            'daily_score_improvement': 0
        }
    else:
        result = await conn.fetchrow("""
            WITH agent_scores AS (
                SELECT 
                    ma.miner_hotkey,
                    ma.created_at,
                    AVG(e.score) as computed_score
                FROM miner_agents ma
                JOIN evaluations e ON ma.version_id = e.version_id
                WHERE e.status = 'completed'
                  AND e.score IS NOT NULL
                  AND e.score > 0
                  AND e.set_id = $1  -- Only use max set_id
                  AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
                  AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                GROUP BY ma.miner_hotkey, ma.created_at, ma.version_id
                HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
            ),
            approved_scores AS (
                SELECT AVG(e.score) as computed_score
                FROM approved_version_ids avi
                JOIN evaluations e ON avi.version_id = e.version_id
                JOIN miner_agents ma ON avi.version_id = ma.version_id
                WHERE e.status = 'completed'
                  AND e.score IS NOT NULL
                  AND e.score > 0
                  AND e.set_id = $1
                  AND e.validator_hotkey NOT LIKE 'i-0%'
                  AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                GROUP BY e.version_id
                HAVING COUNT(DISTINCT e.validator_hotkey) >= 2
            )
            SELECT
                (SELECT COUNT(DISTINCT miner_hotkey) FROM miner_agents WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)) as number_of_agents,
                (SELECT COUNT(*) FROM miner_agents WHERE created_at >= NOW() - INTERVAL '24 hours' AND miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)) as agent_iterations_last_24_hours,
                (SELECT MAX(computed_score) FROM agent_scores) as top_agent_score,
                COALESCE((SELECT MAX(computed_score) FROM agent_scores) - (SELECT MAX(computed_score) FROM approved_scores), 0) as daily_score_improvement;
                 """, max_set_id)
        
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
async def get_top_agents(conn: asyncpg.Connection, num_agents: int = 3) -> list[MinerAgentWithScores]:
    """Get top agents based on scores from the maximum set_id only"""
    
    # First, get the maximum set_id
    max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    if not max_set_id_result or max_set_id_result['max_set_id'] is None:
        return []
    
    max_set_id = max_set_id_result['max_set_id']
    
    # Get top agents based on scores from max set_id only
    results = await conn.fetch("""
        SELECT
            ma.version_id,
            ma.miner_hotkey,
            ma.agent_name,
            ma.version_num,
            ma.created_at,
            ma.status,
            AVG(e.score) AS score,
            e.set_id
        FROM miner_agents ma
        JOIN evaluations e ON ma.version_id = e.version_id
        WHERE e.status = 'completed'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.set_id = $1  -- Only use max set_id
            AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
            AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        GROUP BY ma.version_id, ma.miner_hotkey, ma.agent_name, ma.version_num, ma.created_at, ma.status, e.set_id
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
        ORDER BY AVG(e.score) DESC, ma.created_at ASC
        LIMIT $2;
    """, max_set_id, num_agents)

    return [MinerAgentWithScores(**dict(row)) for row in results]

@db_operation
async def get_agent_summary_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> list[MinerAgentWithScores]:
    results = await conn.fetch("""
       WITH max_set_per_version AS (
            SELECT
                e.version_id,
                MAX(e.set_id) as max_set_id
            FROM evaluations e
            JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE ma.miner_hotkey = $1
            GROUP BY e.version_id
        )
        SELECT
            ma.version_id,
            ma.miner_hotkey,
            ma.agent_name,
            ma.version_num,
            ma.created_at,
            ma.status,
            AVG(e.score) AS score,
            e.set_id
        FROM miner_agents ma
        LEFT JOIN max_set_per_version ms ON ma.version_id = ms.version_id
        LEFT JOIN evaluations e ON ma.version_id = e.version_id
            AND (ms.max_set_id IS NULL OR e.set_id = ms.max_set_id)
            AND e.status = 'completed'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.validator_hotkey NOT LIKE 'i-0%'
        WHERE ma.miner_hotkey = $1
        AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        GROUP BY ma.version_id, ma.miner_hotkey, ma.agent_name, ma.version_num, ma.created_at, ma.status, e.set_id
        HAVING e.set_id IS NULL OR COUNT(DISTINCT e.validator_hotkey) >= 2
        ORDER BY ma.version_num DESC, ma.created_at DESC;
    """, miner_hotkey)

    
    return [MinerAgentWithScores(**dict(row)) for row in results]

@db_operation
async def get_agents_with_scores_by_set_id(conn: asyncpg.Connection, num_agents: int = 10) -> list[dict]:
    """Get agents with their computed scores grouped by set_id"""
    
    results = await conn.fetch("""
        SELECT
            ma.version_id,
            ma.miner_hotkey,
            ma.agent_name,
            ma.version_num,
            ma.created_at,
            ma.status,
            e.set_id,
            AVG(e.score) AS computed_score,
            COUNT(DISTINCT e.validator_hotkey) AS num_validators
        FROM miner_agents ma
        JOIN evaluations e ON ma.version_id = e.version_id
        WHERE e.status = 'completed'
          AND e.score IS NOT NULL
          AND e.score > 0
          AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
          AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        GROUP BY ma.version_id, ma.miner_hotkey, ma.agent_name, ma.version_num, ma.created_at, ma.status, e.set_id
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
        ORDER BY e.set_id DESC, AVG(e.score) DESC, ma.created_at ASC
        LIMIT $1;
    """, num_agents)

    return [dict(row) for row in results]

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
            id, run_id, messages->-1->>'content' as message, temperature, model,cost, response, total_tokens, created_at, finished_at 
        from inferences 
        where run_id = $1;
    """, run_id)

    return [Inference(**dict(run)) for run in runs]