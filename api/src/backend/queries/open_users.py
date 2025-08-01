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

@db_operation
async def create_open_user(conn: asyncpg.Connection, open_user: OpenUser) -> None:
    await conn.execute(
        """
            INSERT INTO open_users (open_hotkey, auth0_user_id, email, name, registered_at)
            VALUES ($1, $2, $3, $4, $5)
        """,
        open_user.open_hotkey,
        open_user.auth0_user_id,
        open_user.email,
        open_user.name,
        open_user.registered_at
    )