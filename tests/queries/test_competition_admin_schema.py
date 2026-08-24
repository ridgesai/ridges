from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

import utils.database as _db
from alembic import command

BASE_REVISION = "07af81b81a3e"
HEAD_REVISION = "6c63d9349cfc"
REPO_ROOT = Path(__file__).resolve().parents[2]


async def _upgrade(revision: str) -> None:
    await asyncio.to_thread(command.upgrade, Config(REPO_ROOT / "alembic.ini"), revision)


async def _downgrade(revision: str) -> None:
    await asyncio.to_thread(command.downgrade, Config(REPO_ROOT / "alembic.ini"), revision)


@pytest.mark.anyio
async def test_competition_admin_event_migration_round_trip(postgres_db) -> None:
    try:
        await _downgrade(BASE_REVISION)
        async with _db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT to_regclass('competition_admin_events')") is None

        await _upgrade(HEAD_REVISION)
        async with _db.pool.acquire() as conn:
            columns = {
                row["column_name"]: row
                for row in await conn.fetch(
                    """
                    SELECT column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'competition_admin_events'
                    """
                )
            }
            assert set(columns) == {
                "event_id",
                "operation",
                "actor",
                "reason",
                "before_state",
                "after_state",
                "created_at",
            }
            assert columns["event_id"]["column_default"] == "gen_random_uuid()"
            assert columns["created_at"]["column_default"] == "now()"
            assert all(column["is_nullable"] == "NO" for column in columns.values())

            constraint_names = {
                row["conname"]
                for row in await conn.fetch(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'competition_admin_events'::regclass
                    """
                )
            }
            assert {
                "competition_admin_events_pkey",
                "ck_competition_admin_events_operation",
                "ck_competition_admin_events_actor_nonblank",
                "ck_competition_admin_events_reason_nonblank",
            } <= constraint_names
            assert not await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'competition_admin_events'::regclass
                      AND contype = 'f'
                )
                """
            )

            event_id = await conn.fetchval(
                """
                INSERT INTO competition_admin_events (
                    operation, actor, reason, before_state, after_state
                ) VALUES ('state', 'admin-key', 'open competition', '{}'::jsonb, '{}'::jsonb)
                RETURNING event_id
                """
            )
            assert event_id is not None

            for statement in (
                "INSERT INTO competition_admin_events "
                "(operation, actor, reason, before_state, after_state) "
                "VALUES ('unknown', 'admin-key', 'reason', '{}'::jsonb, '{}'::jsonb)",
                "INSERT INTO competition_admin_events "
                "(operation, actor, reason, before_state, after_state) "
                "VALUES ('policy', '   ', 'reason', '{}'::jsonb, '{}'::jsonb)",
                "INSERT INTO competition_admin_events "
                "(operation, actor, reason, before_state, after_state) "
                "VALUES ('allocation', 'admin-key', '   ', '{}'::jsonb, '{}'::jsonb)",
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(statement)

        await _downgrade(BASE_REVISION)
        async with _db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT to_regclass('competition_admin_events')") is None
    finally:
        await _upgrade(HEAD_REVISION)
