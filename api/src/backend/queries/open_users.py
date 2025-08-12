import asyncpg
from typing import Optional
from datetime import datetime

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

@db_operation
async def get_open_user_bittensor_hotkey(conn: asyncpg.Connection, open_hotkey: str) -> Optional[str]:
    result = await conn.fetchrow(
        """
            SELECT bittensor_hotkey FROM open_user_bittensor_hotkeys WHERE open_hotkey = $1
            ORDER BY set_at DESC
            LIMIT 1
        """,
        open_hotkey
    )

    if not result:
        return None
    
    return result["bittensor_hotkey"]


@db_operation
async def get_open_agent_periods_on_top(conn: asyncpg.Connection, miner_hotkey: str, hours: float) -> list[tuple[datetime, datetime]]:
    rows = await conn.fetch(
        """
        WITH ranked_top AS (
            SELECT
                ta.created_at AS start_at,
                LEAD(ta.created_at) OVER (ORDER BY ta.created_at) AS next_at,
                ma.miner_hotkey AS miner_hotkey
            FROM top_agents ta
            INNER JOIN miner_agents ma ON ma.version_id = ta.version_id
        ),
        win AS (
            SELECT
                NOW() - make_interval(secs => ($2::double precision) * 3600) AS window_start,
                NOW() AS window_end
        )
        SELECT 
            GREATEST(r.start_at, w.window_start) AS period_start,
            LEAST(COALESCE(r.next_at, w.window_end), w.window_end) AS period_end
        FROM ranked_top r
        CROSS JOIN win w
        WHERE r.miner_hotkey = $1
          AND COALESCE(r.next_at, w.window_end) > w.window_start
          AND r.start_at < w.window_end
        ORDER BY period_start ASC
        """,
        miner_hotkey,
        float(hours),
    )

    return [(row["period_start"], row["period_end"]) for row in rows]

@db_operation
async def get_emission_dispersed_to_open_user(conn: asyncpg.Connection, open_hotkey: str) -> int:
    total = await conn.fetchval(
        """
        SELECT COALESCE(SUM(tt.amount_alpha_rao), 0)
        FROM treasury_transactions tt
        INNER JOIN miner_agents ma ON ma.version_id = tt.version_id
        WHERE ma.miner_hotkey = (
            SELECT oubh.bittensor_hotkey
            FROM open_user_bittensor_hotkeys oubh
            WHERE oubh.open_hotkey = $1
            ORDER BY oubh.set_at DESC
            LIMIT 1
        )
        """,
        open_hotkey,
    )

    return int(total) if total is not None else 0