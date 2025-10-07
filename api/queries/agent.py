import asyncpg

from uuid import UUID
from typing import Optional
from api.src.backend.db_manager import db_operation



@db_operation
async def get_next_agent_id_awaiting_evaluation_for_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[UUID]:
    # TODO: Alex
    if validator_hotkey.startswith("screener-"):
        return None
    else:
        return None