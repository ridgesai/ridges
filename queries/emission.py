from uuid import UUID

import asyncpg

from utils.database import DatabaseConnection, db_operation


@db_operation
async def insert_emission_snapshot(
    conn: DatabaseConnection,
    *,
    hotkey: str,
    agent_id: UUID,
    set_id: int,
    block_number: int,
    emission: float,
) -> None:
    await conn.execute(
        """
        INSERT INTO emission_snapshots (hotkey, agent_id, set_id, block_number, emission)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (hotkey, block_number) DO NOTHING
        """,
        hotkey,
        agent_id,
        set_id,
        block_number,
        emission,
    )


@db_operation
async def get_total_emission_for_approved_agents(conn: DatabaseConnection, set_id: int) -> dict[UUID, float]:
    rows = await conn.fetch(
        """
        SELECT agent_id, SUM(emission) AS total_emission
        FROM emission_snapshots
        WHERE set_id = $1
        GROUP BY agent_id
        """,
        set_id,
    )
    return {row["agent_id"]: float(row["total_emission"]) for row in rows}


@db_operation
async def get_approved_hotkeys_for_set(conn: DatabaseConnection, set_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT DISTINCT ON (a.miner_hotkey)
            a.miner_hotkey, aa.agent_id
        FROM approved_agents aa
        JOIN agents a ON a.agent_id = aa.agent_id
        WHERE aa.set_id = $1
        ORDER BY a.miner_hotkey, aa.approved_at DESC
        """,
        set_id,
    )
