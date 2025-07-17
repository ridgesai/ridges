from typing import List

import asyncpg

from api.src.backend.db_manager import db_operation
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

@db_operation
async def get_latest_set_id(conn: asyncpg.Connection) -> int:
    """Get the latest set_id"""
    result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    return result['max_set_id'] if result and result['max_set_id'] is not None else 0

@db_operation
async def get_evaluation_set_instances(conn: asyncpg.Connection, set_id: int, eval_type: str) -> List[str]:
    """Get all swebench_instance_ids for a given set_id and type"""
    try:
        results = await conn.fetch("""
            SELECT swebench_instance_id
            FROM evaluation_sets 
            WHERE set_id = $1 AND type = $2
            ORDER BY swebench_instance_id
        """, set_id, eval_type)

        return [row['swebench_instance_id'] for row in results]
    except Exception as e:
        logger.error(f"Error retrieving evaluation set instances for set_id {set_id}, type {eval_type}: {e}")
        return []
