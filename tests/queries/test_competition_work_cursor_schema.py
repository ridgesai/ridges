from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

import db.models  # noqa: F401
import utils.database as _db
from alembic import command
from db.base import Base

BASE_REVISION = "6c63d9349cfc"
HEAD_REVISION = "06f4bede4ef6"
REPO_ROOT = Path(__file__).resolve().parents[2]
CURSOR_FAMILIES = (
    "screener_1",
    "screener_2",
    "validator",
    "pre_screening_judge",
    "approval_judge",
)


async def _upgrade(revision: str) -> None:
    await asyncio.to_thread(command.upgrade, Config(REPO_ROOT / "alembic.ini"), revision)


async def _downgrade(revision: str) -> None:
    await asyncio.to_thread(command.downgrade, Config(REPO_ROOT / "alembic.ini"), revision)


@pytest.mark.anyio
async def test_competition_work_cursor_migration_round_trip(postgres_db) -> None:
    try:
        await _downgrade(BASE_REVISION)
        async with _db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT to_regclass('competition_work_cursors')") is None

        await _upgrade(HEAD_REVISION)
        async with _db.pool.acquire() as conn:
            columns = {
                row["column_name"]: row
                for row in await conn.fetch(
                    """
                    SELECT column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'competition_work_cursors'
                    """
                )
            }
            assert set(columns) == {"family", "last_served_set_id"}
            assert columns["family"]["is_nullable"] == "NO"
            assert columns["family"]["data_type"] == "text"
            assert columns["family"]["column_default"] is None
            assert columns["last_served_set_id"]["is_nullable"] == "YES"
            assert columns["last_served_set_id"]["data_type"] == "integer"
            assert columns["last_served_set_id"]["column_default"] is None

            constraint_names = {
                row["conname"]
                for row in await conn.fetch(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'competition_work_cursors'::regclass
                    """
                )
            }
            assert constraint_names == {
                "competition_work_cursors_pkey",
                "ck_competition_work_cursors_family",
            }
            assert not await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'competition_work_cursors'::regclass
                      AND contype = 'f'
                )
                """
            )

            rows = await conn.fetch("SELECT family, last_served_set_id FROM competition_work_cursors ORDER BY family")
            assert [(row["family"], row["last_served_set_id"]) for row in rows] == [
                (family, None) for family in sorted(CURSOR_FAMILIES)
            ]

            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("INSERT INTO competition_work_cursors (family) VALUES ('unknown')")

        await _downgrade(BASE_REVISION)
        async with _db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT to_regclass('competition_work_cursors')") is None
    finally:
        await _upgrade(HEAD_REVISION)


def test_competition_work_cursor_orm_matches_migration_contract() -> None:
    table = Base.metadata.tables["competition_work_cursors"]

    assert set(table.columns.keys()) == {"family", "last_served_set_id"}
    assert table.c.family.primary_key is True
    assert table.c.family.nullable is False
    assert table.c.last_served_set_id.nullable is True
    assert {column.name for column in table.primary_key.columns} == {"family"}
    assert {constraint.name for constraint in table.constraints if constraint.name is not None} == {
        "ck_competition_work_cursors_family"
    }
