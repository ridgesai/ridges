from datetime import datetime, timedelta, timezone

import pytest

import utils.database as _db
from queries.payments import create_payment_quote, reserve_payment


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_payments, upload_payment_quotes RESTART IDENTITY CASCADE")


@pytest.mark.anyio
async def test_create_quote_persists_alpha_amount():
    quote = await create_payment_quote(
        miner_hotkey="5FHkey",
        amount_alpha_rao=120_344_620_287_164,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert quote.amount_alpha_rao == 120_344_620_287_164


@pytest.mark.anyio
async def test_reserve_payment_persists_alpha_amount():
    payment = await reserve_payment(
        payment_block_hash="0xabc",
        payment_extrinsic_index="3",
        miner_hotkey="5FHkey",
        miner_coldkey="5FCold",
        amount_alpha_rao=500_000_000,
    )
    assert payment is not None
    assert payment.amount_alpha_rao == 500_000_000
