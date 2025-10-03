import asyncpg

from typing import Optional
from api.src.backend.db_manager import db_operation



@db_operation
async def get_oldest_version_id_awaiting_screening(conn: asyncpg.Connection, screener_class: int) -> Optional[str]:
    if (screener_class not in [1, 2]):
        raise ValueError(f"Invalid screener number: {screener_class}")

    row = await conn.fetchrow(f"""
        SELECT version_id
        FROM miner_agents
        WHERE status = 'awaiting_screening_{screener_class}'
        AND miner_hotkey NOT IN (
            SELECT miner_hotkey FROM banned_hotkeys
        )
        ORDER BY created_at ASC
        LIMIT 1;
    """)

    if not row:
        # Nobody is awaiting screening
        return None

    return str(row["version_id"])