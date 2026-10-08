import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

import utils.database as db
from alembic import command
from db.base import Base

BASE = "39daf859ec77"
ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.anyio


async def _upgrade_head() -> None:
    await asyncio.to_thread(command.upgrade, Config(ROOT / "alembic.ini"), "head")


async def test_migration_marks_existing_quotes_legacy_and_guards_new_ones(postgres_db):
    try:
        async with db.pool.acquire() as conn:
            await conn.execute("TRUNCATE upload_payment_quotes CASCADE")
        await asyncio.to_thread(command.downgrade, Config(ROOT / "alembic.ini"), BASE)
        async with db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO upload_payment_quotes (miner_hotkey, amount_alpha_rao, expires_at) VALUES ('hk', 1, NOW())"
            )
        await _upgrade_head()
        async with db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT is_legacy FROM upload_payment_quotes") is True
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO upload_payment_quotes (miner_hotkey, amount_alpha_rao, expires_at) "
                    "VALUES ('hk', 1, NOW())"
                )
            await conn.execute("INSERT INTO competitions (set_id) VALUES (77) ON CONFLICT DO NOTHING")
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO competition_upload_prices (set_id, price_usd, target_per_hour, half_life_minutes) "
                    "VALUES (77, 5, 2, 30)"
                )
            await conn.execute("INSERT INTO competition_upload_prices (set_id, price_usd) VALUES (77, 5)")
            row = await conn.fetchrow(
                "SELECT floor_usd, target_per_hour, half_life_minutes FROM competition_upload_prices WHERE set_id = 77"
            )
            assert [float(value) for value in row.values()] == [5.0, 10.0, 30.0]
    finally:
        await _upgrade_head()
        async with db.pool.acquire() as conn:
            await conn.execute("TRUNCATE upload_payment_quotes, competition_upload_prices CASCADE")
            await conn.execute("DELETE FROM competitions WHERE set_id = 77")


async def test_downgrade_refuses_while_competition_bound_quotes_exist(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute("INSERT INTO competitions (set_id) VALUES (78) ON CONFLICT DO NOTHING")
        await conn.execute(
            "INSERT INTO upload_payment_quotes (miner_hotkey, amount_alpha_rao, expires_at, set_id, price_usd, "
            "miner_coldkey) VALUES ('hk', 1, NOW(), 78, 5, 'ck')"
        )
    try:
        with pytest.raises(RuntimeError, match="competition-bound quotes"):
            await asyncio.to_thread(command.downgrade, Config(ROOT / "alembic.ini"), BASE)
    finally:
        async with db.pool.acquire() as conn:
            await conn.execute("TRUNCATE upload_payment_quotes CASCADE")
            await conn.execute("DELETE FROM competitions WHERE set_id = 78")
        await _upgrade_head()


def test_orm_matches_migration_contract():
    prices = Base.metadata.tables["competition_upload_prices"]
    assert set(prices.columns.keys()) == {
        "set_id",
        "floor_usd",
        "target_per_hour",
        "half_life_minutes",
        "price_usd",
        "price_updated_at",
    }
    quotes = Base.metadata.tables["upload_payment_quotes"]
    assert {
        "set_id",
        "price_usd",
        "miner_coldkey",
        "confirmed_at",
        "confirmed_payment_block_hash",
        "confirmed_payment_extrinsic_index",
        "cancelled_at",
        "is_legacy",
    } <= set(quotes.columns.keys())
    names = {constraint.name for constraint in quotes.constraints if constraint.name}
    assert {
        "ck_upload_payment_quotes_bound",
        "ck_upload_payment_quotes_terminal",
        "ck_upload_payment_quotes_confirmed_receipt",
    } <= names
