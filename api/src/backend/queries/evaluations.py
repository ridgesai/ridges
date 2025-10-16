from typing import Optional
import logging
import asyncpg

from utils.database import db_operation
from api.src.backend.entities import Evaluation

logger = logging.getLogger(__name__)

# TODO: ADAM - Used in upload_agent_helpers.py
@db_operation
async def get_running_evaluation_by_miner_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        """
        SELECT e.*
        FROM evaluations e
        JOIN agents ma ON e.agent_id = ma.agent_id
        WHERE ma.miner_hotkey = $1
        AND e.status = 'running'
        ORDER BY e.created_at ASC
        """,
        miner_hotkey
    )
    if not result:
        return None
    if len(result) > 1:
        validators = ", ".join([row[2] for row in result])
        logger.warning(f"Multiple running evaluations found for miner {miner_hotkey} on validators {validators}")
        return None
    
    return Evaluation(**dict(result[0]))