import asyncio
import uuid

import pytest

import utils.database as db
from queries.burn_balance import credit_burn, get_burn_balance, purchase_quote
from queries.competition import initialize_current_competition_policy
from queries.errors import (
    CompetitionNotAcceptingSubmissionsError,
    InsufficientBalanceError,
    QuoteNotConfirmedError,
)
from utils.upload_pricing import PricingSettings, multiplier

pytestmark = pytest.mark.anyio
M = multiplier(PricingSettings())
CK = "5FColdKey456"
RATE = 2.5  # alpha price in USD: a $5 upload is 2e9 rao
BURN_RAO = 2_200_000_000  # $5 quoted with the 1.1 buffer
PRICE_RAO = 2_000_000_000


@pytest.fixture(autouse=True)
async def competition(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE burn_balance_entries, upload_payment_quotes, competitions RESTART IDENTITY CASCADE"
        )
        await conn.execute("INSERT INTO competitions (set_id, start_date) VALUES (1, NOW())")
    await initialize_current_competition_policy()
    yield
    async with db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE burn_balance_entries, upload_payment_quotes, competitions RESTART IDENTITY CASCADE"
        )


async def _quote(*, amount_alpha_rao: int = 2_200_000_000, confirmed: bool = True, coldkey: str = CK) -> uuid.UUID:
    quote_id = uuid.uuid4()
    has_burn = confirmed and amount_alpha_rao > 0
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes
                (quote_id, miner_hotkey, amount_alpha_rao, expires_at, set_id, price_usd, miner_coldkey,
                 confirmed_at, confirmed_payment_block_hash, confirmed_payment_extrinsic_index)
            VALUES ($1, 'hk', $2, clock_timestamp() + interval '15 minutes', 1, 5, $3,
                    CASE WHEN $4 THEN clock_timestamp() END, $5, $6)
            """,
            quote_id,
            amount_alpha_rao,
            coldkey,
            has_burn,
            "0x" + uuid.uuid4().hex * 2 if has_burn else None,
            "1" if has_burn else None,
        )
    return quote_id


async def _price() -> float | None:
    async with db.pool.acquire() as conn:
        return await conn.fetchval("SELECT price_usd::float8 FROM competition_upload_prices WHERE set_id = 1")


async def _credit(quote_id: uuid.UUID, rao: int = BURN_RAO) -> None:
    async with db.pool.acquire() as raw:
        await credit_burn(db.DatabaseConnection(raw, "test"), quote_id=quote_id, miner_coldkey=CK, amount_alpha_rao=rao)


async def _purchase(quote_id: uuid.UUID) -> str:
    return await purchase_quote(quote_id, miner_coldkey=CK, alpha_price_usd=RATE)


async def test_credit_then_purchase_debits_price_and_bumps_once():
    quote_id = await _quote()
    await _credit(quote_id)
    assert await get_burn_balance(CK) == BURN_RAO
    assert await _purchase(quote_id) == "purchased"
    assert await get_burn_balance(CK) == BURN_RAO - PRICE_RAO
    assert await _price() == pytest.approx(5 * M, rel=1e-3)
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT purchased_at, purchase_price_usd::float8 AS p, purchase_price_alpha_rao FROM upload_payment_quotes"
        )
    assert (
        row["purchased_at"] is not None
        and row["p"] == pytest.approx(5.0)
        and row["purchase_price_alpha_rao"] == PRICE_RAO
    )


async def test_insufficient_balance_writes_nothing():
    quote_id = await _quote()
    await _credit(quote_id, 1_200_000_000)
    with pytest.raises(InsufficientBalanceError) as exc:
        await _purchase(quote_id)
    assert exc.value.price_usd == pytest.approx(5.0)
    assert (exc.value.price_alpha_rao, exc.value.balance_alpha_rao, exc.value.shortfall_alpha_rao) == (
        PRICE_RAO,
        1_200_000_000,
        800_000_000,
    )
    assert await get_burn_balance(CK) == 1_200_000_000
    assert await _price() is None


async def test_purchase_is_idempotent():
    quote_id = await _quote()
    await _credit(quote_id, 8_000_000_000)
    await _purchase(quote_id)
    assert await _purchase(quote_id) == "already_purchased"
    assert await get_burn_balance(CK) == 6_000_000_000
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_zero_quote_purchases_from_existing_balance():
    funded = await _quote()
    await _credit(funded, 4_800_000_000)
    zero = await _quote(amount_alpha_rao=0, confirmed=False)
    assert await _purchase(zero) == "purchased"
    assert await get_burn_balance(CK) == 2_800_000_000


async def test_unconfirmed_burn_quote_cannot_purchase():
    quote_id = await _quote(confirmed=False)
    with pytest.raises(QuoteNotConfirmedError):
        await _purchase(quote_id)


async def test_concurrent_purchases_spend_the_balance_once():
    first, second = await _quote(), await _quote(amount_alpha_rao=2_200_000_001)
    await _credit(first, BURN_RAO)
    await _credit(second, 120_000_000)
    results = await asyncio.gather(_purchase(first), _purchase(second), return_exceptions=True)
    assert sorted(type(result).__name__ for result in results) == ["InsufficientBalanceError", "str"]
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_competition_not_accepting_blocks_purchase():
    quote_id = await _quote()
    await _credit(quote_id, 4_000_000_000)
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET submissions_closed_at = NOW(), emissions_end_at = NOW()")
    with pytest.raises(CompetitionNotAcceptingSubmissionsError):
        await _purchase(quote_id)
    assert await get_burn_balance(CK) == 4_000_000_000


async def test_unredeemed_purchase_is_refunded_once_competition_closes():
    quote_id = await _quote()
    await _credit(quote_id, BURN_RAO)
    await _purchase(quote_id)
    assert await get_burn_balance(CK) == BURN_RAO - PRICE_RAO
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET submissions_closed_at = NOW(), emissions_end_at = NOW()")
    assert await get_burn_balance(CK) == BURN_RAO
    assert await get_burn_balance(CK) == BURN_RAO
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT refunded_at FROM upload_payment_quotes WHERE quote_id = $1", quote_id)
