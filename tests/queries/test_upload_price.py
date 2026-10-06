import asyncio

import pytest

import utils.database as db
from queries.upload_price import apply_bump, get_competition_price, update_competition_pricing
from utils.upload_pricing import PricingSettings, multiplier

pytestmark = pytest.mark.anyio
M = multiplier(PricingSettings())


@pytest.fixture(autouse=True)
async def competition(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute("TRUNCATE competitions RESTART IDENTITY CASCADE")
        await conn.execute("INSERT INTO competitions (set_id) VALUES (1)")
    yield
    async with db.pool.acquire() as conn:
        await conn.execute("TRUNCATE competitions RESTART IDENTITY CASCADE")


async def _bump(set_id: int = 1):
    async with db.pool.acquire() as raw:
        async with raw.transaction():
            return await apply_bump(db.DatabaseConnection(raw, "test"), set_id)


async def _stored_price() -> float:
    async with db.pool.acquire() as conn:
        return await conn.fetchval("SELECT price_usd::float8 FROM competition_upload_prices WHERE set_id = 1")


async def test_first_bump_creates_row_with_defaults():
    await _bump()
    assert await _stored_price() == pytest.approx(5 * M)


async def test_price_decays_from_last_bump():
    await _bump()
    async with db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE competition_upload_prices "
            "SET price_usd = 20, price_updated_at = clock_timestamp() - interval '30 minutes'"
        )
    assert (await get_competition_price(1)).price_usd == pytest.approx(10, rel=1e-3)


async def test_concurrent_bumps_both_apply():
    await asyncio.gather(_bump(), _bump())
    assert await _stored_price() == pytest.approx(5 * M * M, rel=1e-3)


async def test_settings_update_materialises_price_and_raises_to_new_floor():
    await _bump()
    raised = await update_competition_pricing(1, PricingSettings(floor_usd=12))
    assert raised.price_usd == 12
    assert (await get_competition_price(1)).settings.floor_usd == 12
    lowered = await update_competition_pricing(1, PricingSettings(floor_usd=1))
    assert lowered.price_usd == pytest.approx(12, rel=1e-3)


async def test_settings_update_for_missing_competition_returns_none():
    assert await update_competition_pricing(999, PricingSettings()) is None
