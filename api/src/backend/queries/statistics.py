from typing import Any, Optional
from datetime import datetime

from pydantic import BaseModel
from uuid import UUID
import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgent, Inference, MinerAgentWithScores

@db_operation
async def get_24_hour_statistics(conn: asyncpg.Connection) -> dict[str, Any]:
    """
    Get 24-hour statistics using the agent_scores materialized view
    """
    from api.src.backend.entities import MinerAgentScored
    
    return await MinerAgentScored.get_24_hour_statistics(conn)

class RunningEvaluation(BaseModel):
    version_id: UUID
    validator_hotkey: str
    started_at: datetime
    agent_name: str
    miner_hotkey: str
    version_num: int
    progress: float

@db_operation
async def get_currently_running_evaluations(conn: asyncpg.Connection) -> list[RunningEvaluation]:
    results = await conn.fetch("""
        select 
            e.evaluation_id,
            e.version_id, 
            e.validator_hotkey, 
            e.started_at, 
            a.agent_name, 
            a.miner_hotkey, 
            a.version_num,
            COALESCE(AVG(
                CASE r.status
                    WHEN 'started' THEN 0.2
                    WHEN 'sandbox_created' THEN 0.4
                    WHEN 'patch_generated' THEN 0.6
                    WHEN 'eval_started' THEN 0.8
                    WHEN 'result_scored' THEN 1.0
                    WHEN 'error' THEN 1.0
                    ELSE 0.0
                END
            ), 0.0) as progress
        from evaluations e
        left join miner_agents a on a.version_id = e.version_id
        left join evaluation_runs r on r.evaluation_id = e.evaluation_id 
            and r.status not in ('cancelled')
        where e.status = 'running'
        group by e.evaluation_id, e.version_id, e.validator_hotkey, e.started_at, 
                 a.agent_name, a.miner_hotkey, a.version_num;
    """)

    return [RunningEvaluation(**{k: v for k, v in dict(row).items() if k != 'evaluation_id'}) for row in results]

@db_operation
async def get_top_agents(conn: asyncpg.Connection, num_agents: int = 3, search_term: Optional[str] = None, filter_for_open_user: bool = False, filter_for_registered_user: bool = False, filter_for_approved: bool = False) -> list[MinerAgentWithScores]:
    """Get top agents using the agent_scores materialized view"""
    from api.src.backend.entities import MinerAgentScored
    
    return await MinerAgentScored.get_top_agents(conn, num_agents, search_term, filter_for_open_user, filter_for_registered_user, filter_for_approved)

@db_operation
async def get_agent_summary_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> list[MinerAgentWithScores]:
    """Get agent summary by hotkey using the agent_scores materialized view where available"""
    from api.src.backend.entities import MinerAgentScored
    
    return await MinerAgentScored.get_agent_summary_by_hotkey(conn, miner_hotkey)

@db_operation
async def get_agents_with_scores_by_set_id(conn: asyncpg.Connection, num_agents: int = 10) -> list[dict]:
    """Get agents with their scores grouped by set_id using the materialized view"""
    from api.src.backend.entities import MinerAgentScored
    
    return await MinerAgentScored.get_agents_with_scores_by_set_id(conn, num_agents)

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
                    ORDER BY     e.screener_score DESC NULLS LAST, e.created_at
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
            id, run_id, 
            (SELECT message->>'content' FROM jsonb_array_elements(messages) WITH ORDINALITY AS t(message, index) 
             WHERE message->>'role' = 'user' 
             ORDER BY index DESC LIMIT 1) as message, 
            temperature, model, cost, response, total_tokens, created_at, finished_at, provider, status_code
        from inferences 
        where run_id = $1;
    """, run_id)

    return [Inference(**dict(run)) for run in runs]

@db_operation
async def get_agent_scores_over_time(conn: asyncpg.Connection, set_id: Optional[int] = None) -> list[dict]:
    """Get agent scores over time for charting"""
    # Use max set_id if not provided
    if set_id is None:
        set_id_query = "SELECT MAX(set_id) FROM evaluations"
        set_id = await conn.fetchval(set_id_query)
    
    # Get comprehensive data from miner_agents and evaluations
    query = """
        WITH hourly_data AS (
            SELECT 
                DATE_TRUNC('hour', ma.created_at) as hour,
                ma.miner_hotkey,
                ma.version_id,
                e.score
            FROM miner_agents ma
            LEFT JOIN evaluations e ON ma.version_id = e.version_id 
                AND e.set_id = $1 
                AND e.status = 'completed' 
                AND e.score IS NOT NULL
                AND e.validator_hotkey NOT LIKE 'screener-%' 
                AND e.validator_hotkey NOT LIKE 'i-0%'
            WHERE ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        ),
        hourly_stats AS (
            SELECT 
                hour,
                COUNT(DISTINCT miner_hotkey) as active_miners,
                COUNT(DISTINCT CASE WHEN score IS NOT NULL THEN miner_hotkey END) as scored_miners,
                AVG(score) as avg_score,
                PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY score) as top_10_percent,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY score) as top_25_percent,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY score) as median_score,
                MIN(score) as min_score,
                MAX(score) as max_score,
                COUNT(score) as total_evaluations
            FROM hourly_data
            GROUP BY hour
        )
        SELECT 
            hour,
            active_miners,
            scored_miners,
            total_evaluations,
            ROUND(COALESCE(avg_score, 0)::numeric, 3) as average_score,
            ROUND(COALESCE(top_10_percent, 0)::numeric, 3) as top_10_percent,
            ROUND(COALESCE(top_25_percent, 0)::numeric, 3) as top_25_percent,
            ROUND(COALESCE(median_score, 0)::numeric, 3) as median_score,
            ROUND(COALESCE(min_score, 0)::numeric, 3) as min_score,
            ROUND(COALESCE(max_score, 0)::numeric, 3) as max_score
        FROM hourly_stats
        WHERE hour IS NOT NULL
        ORDER BY hour
    """
    rows = await conn.fetch(query, set_id)
    return [dict(row) for row in rows]

@db_operation
async def get_miner_score_activity(conn: asyncpg.Connection, set_id: Optional[int] = None) -> list[dict]:
    """Get miner submissions and top scores by hour for correlation analysis"""
    # Use max set_id if not provided
    if set_id is None:
        set_id_query = "SELECT MAX(set_id) FROM agent_scores"
        set_id = await conn.fetchval(set_id_query)
    
    query = """
        WITH hourly_submissions AS (
            SELECT 
                DATE_TRUNC('hour', created_at) as hour,
                COUNT(DISTINCT version_id) as miner_submissions
            FROM miner_agents
            WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            GROUP BY DATE_TRUNC('hour', created_at)
        ),
        hourly_scores AS (
            SELECT 
                DATE_TRUNC('hour', created_at) as hour,
                MAX(final_score) as hour_max_score
            FROM agent_scores
            WHERE set_id = $1 AND final_score IS NOT NULL
            GROUP BY DATE_TRUNC('hour', created_at)
            
            UNION ALL
            
            SELECT 
                DATE_TRUNC('hour', ma.created_at) as hour,
                AVG(e.score) as hour_max_score
            FROM miner_agents ma
            LEFT JOIN evaluations e ON ma.version_id = e.version_id 
                AND e.set_id = $1 
                AND e.status = 'completed' 
                AND e.score IS NOT NULL
                AND e.validator_hotkey NOT LIKE 'screener-%' 
                AND e.validator_hotkey NOT LIKE 'i-0%'
            WHERE ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND e.score IS NOT NULL
            AND DATE_TRUNC('hour', ma.created_at) NOT IN (
                SELECT DATE_TRUNC('hour', created_at) 
                FROM agent_scores 
                WHERE set_id = $1 AND final_score IS NOT NULL
            )
            GROUP BY DATE_TRUNC('hour', ma.created_at)
        ),
        combined_hourly AS (
            SELECT 
                hs.hour,
                hs.miner_submissions,
                COALESCE(hsc.hour_max_score, 0) as hour_score
            FROM hourly_submissions hs
            LEFT JOIN hourly_scores hsc ON hs.hour = hsc.hour
        )
        SELECT 
            hour,
            miner_submissions,
            GREATEST(
                COALESCE(
                    MAX(hour_score) OVER (ORDER BY hour ROWS UNBOUNDED PRECEDING), 
                    0
                ), 
                0
            ) as top_score
        FROM combined_hourly
        WHERE hour IS NOT NULL
        ORDER BY hour
    """
    rows = await conn.fetch(query, set_id)
    return [{"hour": row["hour"], "miner_submissions": row["miner_submissions"], "top_score": float(row["top_score"] or 0)} for row in rows]
