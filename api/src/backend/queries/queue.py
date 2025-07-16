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
                validator_hotkey,
                COUNT(*) as queue_size,
                json_agg(
                    json_build_object(
                        'evaluation_id', evaluation_id,
                        'version_id', version_id,
                        'created_at', created_at
                    ) ORDER BY created_at ASC
                ) as queue_items
            FROM evaluations
            WHERE status = 'waiting'
            GROUP BY validator_hotkey
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
                created_at=item['created_at']
            ))
        
        validator_queues.append(ValidatorQueueInfo(
            validator_hotkey=queue_row['validator_hotkey'],
            queue_size=queue_row['queue_size'],
            queue=queue_items
        ))

    return validator_queues
