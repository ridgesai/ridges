import logging
import asyncpg
from utils.database import db_operation


logger = logging.getLogger(__name__)

# TODO: ADAM - used in endpoints/open_users.py
@db_operation
async def get_treasury_hotkeys(conn: asyncpg.Connection) -> list[str]:
    """
    Returns a list of all treasury hotkeys
    """
    rows = await conn.fetch("""
        SELECT hotkey FROM treasury_wallets WHERE active = TRUE
    """)
    return [r["hotkey"] for r in rows]