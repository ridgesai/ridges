import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import utils.database as db
from queries.upload_price import (
    apply_bump,
    get_competition_price,
    get_competition_price_history,
    update_competition_pricing,
)
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


async def _purchased(set_id: int, minutes_ago: float, price_usd: float) -> None:
    """A balance-paid purchase (nothing burned), as purchase_quote leaves it."""
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes
                (quote_id, miner_hotkey, amount_alpha_rao, expires_at, set_id, price_usd, miner_coldkey,
                 purchased_at, purchase_price_usd, purchase_price_alpha_rao)
            VALUES ($1, 'hk', 0, clock_timestamp() + interval '15 minutes', $2, $3, 'ck',
                    clock_timestamp() - make_interval(secs => $4), $3, 1000)
            """,
            uuid.uuid4(),
            set_id,
            price_usd,
            minutes_ago * 60,
        )


async def test_price_history_is_the_window_led_by_the_purchase_before_it():
    async with db.pool.acquire() as conn:
        await conn.execute("INSERT INTO competitions (set_id) VALUES (2)")
    await _purchased(1, 300, 5.0)
    await _purchased(1, 180, 5.5)
    await _purchased(1, 50, 5.74)
    await _purchased(1, 10, 6.1)
    await _purchased(2, 20, 9.0)

    history = await get_competition_price_history(1, datetime.now(timezone.utc) - timedelta(hours=1))

    assert [price for _, price in history.purchases] == [5.5, 5.74, 6.1], "anchor first, then the window"
    assert [at for at, _ in history.purchases] == sorted(at for at, _ in history.purchases)
    assert history.settings == PricingSettings()
    assert history.price_usd == 5.0, "no price row was bumped in this test, so the default floor"


async def test_price_history_without_purchases_is_empty():
    history = await get_competition_price_history(1, datetime.now(timezone.utc) - timedelta(hours=1))
    assert history.purchases == [] and history.price_usd == 5.0


async def test_price_history_for_missing_competition_returns_none():
    assert await get_competition_price_history(999, datetime.now(timezone.utc)) is None
