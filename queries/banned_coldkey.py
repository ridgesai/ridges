from typing import Optional
from uuid import UUID

from models.banned_coldkey import BannedColdkey
from utils.database import DatabaseConnection, db_operation

COLDKEY_BAN_LOCK_NAMESPACE = -1729


async def lock_coldkey_ban_state(conn: DatabaseConnection, miner_coldkey: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1, hashtext($2))",
        COLDKEY_BAN_LOCK_NAMESPACE,
        miner_coldkey,
    )


@db_operation
async def get_banned_coldkey(
    conn: DatabaseConnection,
    miner_coldkey: str,
) -> Optional[BannedColdkey]:
    row = await conn.fetchrow(
        "SELECT * FROM banned_coldkeys WHERE miner_coldkey = $1",
        miner_coldkey,
    )
    return BannedColdkey(**row) if row is not None else None


@db_operation
async def is_agent_coldkey_banned(conn: DatabaseConnection, agent_id: UUID) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM agents
            INNER JOIN banned_coldkeys USING (miner_coldkey)
            WHERE agents.agent_id = $1
        )
        """,
        agent_id,
    )


@db_operation
async def ban_coldkey(
    conn: DatabaseConnection,
    miner_coldkey: str,
    banned_reason: str,
) -> BannedColdkey:
    async with conn.conn.transaction():
        await lock_coldkey_ban_state(conn, miner_coldkey)
        row = await conn.fetchrow(
            """
            INSERT INTO banned_coldkeys (miner_coldkey, banned_reason)
            VALUES ($1, $2)
            ON CONFLICT (miner_coldkey) DO UPDATE
            SET banned_reason = EXCLUDED.banned_reason
            RETURNING *
            """,
            miner_coldkey,
            banned_reason,
        )
    return BannedColdkey(**row)


@db_operation
async def unban_coldkey(conn: DatabaseConnection, miner_coldkey: str) -> bool:
    async with conn.conn.transaction():
        await lock_coldkey_ban_state(conn, miner_coldkey)
        result = await conn.execute(
            "DELETE FROM banned_coldkeys WHERE miner_coldkey = $1",
            miner_coldkey,
        )
    return result == "DELETE 1"
