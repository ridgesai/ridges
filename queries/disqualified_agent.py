from typing import Optional
from uuid import UUID

from models.disqualified_agent import DisqualifiedAgent
from utils.database import DatabaseConnection, db_operation

DISQUALIFIED_AGENT_LOCK_NAMESPACE = -1731


async def lock_disqualified_agent_state(conn: DatabaseConnection, agent_id: UUID) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1, hashtext($2))",
        DISQUALIFIED_AGENT_LOCK_NAMESPACE,
        str(agent_id),
    )


@db_operation
async def get_disqualified_agent(
    conn: DatabaseConnection,
    agent_id: UUID,
) -> Optional[DisqualifiedAgent]:
    row = await conn.fetchrow(
        "SELECT * FROM disqualified_agents WHERE agent_id = $1",
        agent_id,
    )
    return DisqualifiedAgent(**row) if row is not None else None


@db_operation
async def disqualify_agent(
    conn: DatabaseConnection,
    agent_id: UUID,
    reason: str,
) -> DisqualifiedAgent:
    async with conn.conn.transaction():
        await lock_disqualified_agent_state(conn, agent_id)
        row = await conn.fetchrow(
            """
            INSERT INTO disqualified_agents (agent_id, reason)
            VALUES ($1, $2)
            ON CONFLICT (agent_id) DO UPDATE
            SET reason = EXCLUDED.reason
            RETURNING *
            """,
            agent_id,
            reason,
        )
    return DisqualifiedAgent(**row)
