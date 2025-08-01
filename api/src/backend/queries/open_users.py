import asyncpg
from typing import Optional

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import OpenUser

@db_operation
async def get_open_user(conn: asyncpg.Connection, auth0_user_id: str) -> Optional[OpenUser]:
    result = await conn.fetchrow(
        """
            SELECT * FROM open_users WHERE auth0_user_id = $1
        """,
        auth0_user_id
    )
    
    if not result:
        return None
    
    return OpenUser(**dict(result))