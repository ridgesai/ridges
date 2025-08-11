import asyncpg
import json

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import ValidatorQueueInfo, EvaluationQueueItem, ScreenerQueueByStage, ScreenerQueueAgent


@db_operation
async def get_queue_for_all_validators(
    conn: asyncpg.Connection
) -> list[ValidatorQueueInfo]:
    queue_rows = await conn.fetch(
        """
            SELECT
                e.validator_hotkey,
                COUNT(*) as queue_size,
                json_agg(
                    json_build_object(
                        'evaluation_id', e.evaluation_id,
                        'version_id', e.version_id,
                        'miner_hotkey', m.miner_hotkey,
                        'agent_name', m.agent_name,
                        'created_at', e.created_at,
                        'screener_score', e.screener_score
                    ) ORDER BY e.screener_score DESC NULLS LAST, e.created_at ASC
                ) as queue_items
            FROM evaluations e
            JOIN miner_agents m ON e.version_id = m.version_id
            WHERE e.status = 'waiting'
            AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND m.miner_hotkey NOT IN (SELECT miner_hotkey from banned_hotkeys)
            AND m.status NOT IN ('pruned', 'replaced')
            GROUP BY e.validator_hotkey
            ORDER BY queue_size DESC;
        """,
    )

    validator_queues = []
    for queue_row in queue_rows:
        queue_items_data = json.loads(queue_row['queue_items']) if queue_row['queue_items'] else []
        
        queue_items = []
        for item in queue_items_data:
            queue_items.append(EvaluationQueueItem(
                evaluation_id=item['evaluation_id'],
                version_id=item['version_id'],
                miner_hotkey=item['miner_hotkey'],
                agent_name=item['agent_name'],
                created_at=item['created_at'],
                screener_score=item['screener_score']
            ))
        
        validator_queues.append(ValidatorQueueInfo(
            validator_hotkey=queue_row['validator_hotkey'],
            queue_size=queue_row['queue_size'],
            queue=queue_items
        ))

    return validator_queues


@db_operation
async def get_screener_queue_by_stage(conn: asyncpg.Connection) -> ScreenerQueueByStage:
    """Get miner agents awaiting screening by stage"""
    
    # Get stage 1 queue
    stage1_rows = await conn.fetch(
        """
        SELECT 
            version_id,
            miner_hotkey,
            agent_name,
            version_num,
            created_at,
            status
        FROM miner_agents 
        WHERE status = 'awaiting_screening_1'
        AND miner_hotkey NOT IN (SELECT miner_hotkey from banned_hotkeys)
        AND status NOT IN ('pruned', 'replaced')
        ORDER BY created_at ASC
        """,
    )
    
    # Get stage 2 queue  
    stage2_rows = await conn.fetch(
        """
        SELECT 
            version_id,
            miner_hotkey,
            agent_name,
            version_num,
            created_at,
            status
        FROM miner_agents 
        WHERE status = 'awaiting_screening_2'
        AND miner_hotkey NOT IN (SELECT miner_hotkey from banned_hotkeys)
        AND status NOT IN ('pruned', 'replaced')
        ORDER BY created_at ASC
        """,
    )
    
    # Convert to ScreenerQueueAgent objects
    stage1_agents = [ScreenerQueueAgent(**dict(row)) for row in stage1_rows]
    stage2_agents = [ScreenerQueueAgent(**dict(row)) for row in stage2_rows]
    
    return ScreenerQueueByStage(
        stage_1=stage1_agents,
        stage_2=stage2_agents
    )
