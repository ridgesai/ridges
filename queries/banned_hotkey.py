from typing import Optional

from models.banned_hotkey import BannedHotkey
from utils.database import db_operation, DatabaseConnection



@db_operation
async def get_banned_hotkey(conn: DatabaseConnection, miner_hotkey: str) -> Optional[BannedHotkey]:
    banned_hotkey = await conn.fetchrow(
        """
        SELECT * FROM banned_hotkeys WHERE miner_hotkey = $1
        """,
        miner_hotkey
    )

    if not banned_hotkey:
        return None

    return BannedHotkey(**banned_hotkey)
