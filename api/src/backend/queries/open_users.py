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

@db_operation
async def get_open_user_by_hotkey(conn: asyncpg.Connection, open_hotkey: str) -> Optional[OpenUser]:
    result = await conn.fetchrow(
        """
            SELECT * FROM open_users WHERE open_hotkey = $1
        """,
        open_hotkey
    )

    if not result:
        return None
    
    return OpenUser(**dict(result))

@db_operation
async def check_open_user_email_in_whitelist(conn: asyncpg.Connection, email: str) -> bool:
    result = await conn.fetchrow(
        """
            SELECT email FROM open_user_email_whitelist WHERE email = $1
        """,
        email
    )
    
    if not result:
        return False
    
    return True

@db_operation
async def add_open_user_email_to_whitelist(conn: asyncpg.Connection, email: str) -> None:
    await conn.execute(
        """
            INSERT INTO open_user_email_whitelist (email) VALUES ($1)
            ON CONFLICT (email) DO NOTHING
        """,
        email)

@db_operation
async def get_open_user_by_email(conn: asyncpg.Connection, email: str) -> Optional[OpenUser]:
    result = await conn.fetchrow(
        """
            SELECT * FROM open_users WHERE email = $1
        """,
        email
    )
    
    if not result:
        return None
    
    return OpenUser(**dict(result))

@db_operation
async def update_open_user_bittensor_hotkey(conn: asyncpg.Connection, open_hotkey: str, bittensor_hotkey: str) -> None:
    await conn.execute(
        """
            INSERT INTO open_user_bittensor_hotkeys (open_hotkey, bittensor_hotkey)
            VALUES ($1, $2)
        """,
        open_hotkey,
        bittensor_hotkey
    )
