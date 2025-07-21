import asyncpg
import json

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import ValidatorQueueInfo, EvaluationQueueItem


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
                        'created_at', e.created_at
                    ) ORDER BY e.created_at ASC
                ) as queue_items
            FROM evaluations e
            JOIN miner_agents m ON e.version_id = m.version_id
            WHERE e.status = 'waiting'
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
                created_at=item['created_at']
            ))
        
        validator_queues.append(ValidatorQueueInfo(
            validator_hotkey=queue_row['validator_hotkey'],
            queue_size=queue_row['queue_size'],
            queue=queue_items
        ))

    return validator_queues
