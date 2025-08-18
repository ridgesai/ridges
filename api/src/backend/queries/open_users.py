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
async def get_open_agent_periods_on_top(conn: asyncpg.Connection, miner_hotkey: str) -> list[tuple[datetime, datetime]]:
    rows = await conn.fetch(
        """
        WITH top_intervals AS (
            SELECT
                ta.version_id,
                ta.created_at AS span_start,
                LEAD(ta.created_at) OVER (ORDER BY ta.created_at, ta.id) AS span_end
            FROM top_agents ta
        ),
        events_top AS (
            SELECT
                ti.version_id,
                ti.span_start AS event_time
            FROM top_intervals ti
            JOIN approved_version_ids avi
                ON avi.version_id = ti.version_id
               AND avi.approved_at <= ti.span_start
        ),
        events_approval AS (
            SELECT
                ti.version_id,
                avi.approved_at AS event_time
            FROM top_intervals ti
            JOIN approved_version_ids avi
                ON avi.version_id = ti.version_id
               AND avi.approved_at > ti.span_start
               AND avi.approved_at < COALESCE(ti.span_end, NOW())
        ),
        events AS (
            SELECT DISTINCT version_id, event_time FROM (
                SELECT * FROM events_top
                UNION ALL
                SELECT * FROM events_approval
            ) x
        ),
        ordered AS (
            SELECT
                version_id,
                event_time,
                LAG(version_id) OVER (ORDER BY event_time) AS prev_version_id
            FROM events
        ),
        change_points AS (
            SELECT version_id, event_time
            FROM ordered
            WHERE prev_version_id IS DISTINCT FROM version_id OR prev_version_id IS NULL
        ),
        spans AS (
            SELECT
                version_id,
                event_time AS top_start_at,
                LEAD(event_time) OVER (ORDER BY event_time) AS top_end_at
            FROM change_points
        )
        SELECT
            s.top_start_at AS period_start,
            COALESCE(s.top_end_at, NOW()) AS period_end
        FROM spans s
        JOIN miner_agents ma ON ma.version_id = s.version_id
        JOIN approved_version_ids avi ON avi.version_id = s.version_id
        WHERE ma.miner_hotkey = $1
          AND s.top_start_at >= avi.approved_at
        ORDER BY period_start ASC
        """,
        miner_hotkey,
    )

    return [(row["period_start"], row["period_end"]) for row in rows]

@db_operation
async def get_emission_dispersed_to_open_user(conn: asyncpg.Connection, open_hotkey: str) -> int:
    total = await conn.fetchval(
        """
        SELECT COALESCE(SUM(tt.amount_alpha_rao), 0)
        FROM treasury_transactions tt
        INNER JOIN miner_agents ma ON ma.version_id = tt.version_id
        WHERE ma.miner_hotkey = $1
        """,
        open_hotkey,
    )

    return int(total) if total is not None else 0

@db_operation
async def get_treasury_transactions_for_open_user(conn: asyncpg.Connection, open_hotkey: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT 
            tt.sender_coldkey,
            tt.destination_coldkey,
            tt.staker_hotkey,
            tt.amount_alpha_rao,
            tt.occurred_at,
            tt.version_id,
            tt.extrinsic_code,
            tt.fee
        FROM treasury_transactions tt
        INNER JOIN miner_agents ma ON ma.version_id = tt.version_id
        WHERE ma.miner_hotkey = $1
        ORDER BY tt.occurred_at DESC
        """,
        open_hotkey,
    )

    return [
        {
            "sender_coldkey": str(row["sender_coldkey"]),
            "destination_coldkey": str(row["destination_coldkey"]),
            "staker_hotkey": str(row["staker_hotkey"]),
            "amount_alpha": int(row["amount_alpha_rao"]) if row["amount_alpha_rao"] is not None else 0,
            "occurred_at": str(row["occurred_at"]),
            "version_id": str(row["version_id"]),
            "extrinsic_code": str(row["extrinsic_code"]),
            "fee": bool(row["fee"])
        }
        for row in rows
    ]

@db_operation
async def get_all_transactions(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT 
            tt.sender_coldkey,
            tt.destination_coldkey,
            tt.staker_hotkey,
            tt.amount_alpha_rao,
            tt.version_id AS transaction_version_id,
            tt.occurred_at,
            tt.extrinsic_code,
            tt.fee,
            ma.version_id AS agent_version_id,
            ma.miner_hotkey,
            ma.agent_name,
            ma.version_num,
            ma.created_at
        FROM treasury_transactions tt
        INNER JOIN miner_agents ma ON ma.version_id = tt.version_id
        ORDER BY tt.occurred_at DESC
        """,
    )

    return [
        {
            "sender_coldkey": str(row["sender_coldkey"]),
            "destination_coldkey": str(row["destination_coldkey"]),
            "staker_hotkey": str(row["staker_hotkey"]),
            "amount_alpha": int(row["amount_alpha_rao"]),
            "transaction_version_id": str(row["transaction_version_id"]),
            "occurred_at": str(row["occurred_at"]),
            "extrinsic_code": str(row["extrinsic_code"]),
            "fee": bool(row["fee"]),
            "agent_version_id": str(row["agent_version_id"]),
            "miner_hotkey": str(row["miner_hotkey"]),
            "agent_name": str(row["agent_name"]),
            "version_num": int(row["version_num"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]