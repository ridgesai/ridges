from __future__ import annotations

from uuid import UUID

from models.competition import CompetitionState
from queries.banned_coldkey import get_banned_coldkey, lock_coldkey_ban_state
from queries.competition import lock_competition_for_admission
from queries.errors import (
    ColdkeyBannedError,
    CompetitionNotAcceptingSubmissionsError,
    InsufficientBalanceError,
    QuoteCancelledError,
    QuoteNotConfirmedError,
)
from queries.upload_price import apply_bump, lock_competition_price
from utils.database import DatabaseConnection, db_operation
from utils.upload_pricing import exact_alpha_rao_for_usd

BALANCE_LOCK_NAMESPACE = -1732


async def lock_balance(conn: DatabaseConnection, miner_coldkey: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock($1, hashtext($2))", BALANCE_LOCK_NAMESPACE, miner_coldkey)


async def balance_alpha_rao(conn: DatabaseConnection, miner_coldkey: str) -> int:
    return await conn.fetchval(
        "SELECT COALESCE(SUM(amount_alpha_rao), 0)::bigint FROM burn_balance_entries WHERE miner_coldkey = $1",
        miner_coldkey,
    )


async def credit_burn(conn: DatabaseConnection, *, quote_id: UUID, miner_coldkey: str, amount_alpha_rao: int) -> None:
    """Credit a confirmed burn. Idempotent per quote."""
    await conn.execute(
        "INSERT INTO burn_balance_entries (miner_coldkey, quote_id, kind, amount_alpha_rao) "
        "VALUES ($1, $2, 'burn', $3) ON CONFLICT (quote_id, kind) DO NOTHING",
        miner_coldkey,
        quote_id,
        amount_alpha_rao,
    )


@db_operation
async def get_burn_balance(conn: DatabaseConnection, miner_coldkey: str) -> int:
    async with conn.conn.transaction():
        await lock_balance(conn, miner_coldkey)
        return await balance_alpha_rao(conn, miner_coldkey)


@db_operation
async def purchase_quote(
    conn: DatabaseConnection, quote_id: UUID, *, miner_coldkey: str, alpha_price_usd: float
) -> str:
    """Buy the upload a quote is for, from `miner_coldkey`'s balance, at the competition's price right now.

    `miner_coldkey` is the hotkey's current owner (looked up by the caller), so a hotkey that changed owner
    can only spend its new owner's balance. The USD price is converted to alpha at `alpha_price_usd` with no
    buffer. Returns "purchased" or "already_purchased". The one place the price is bumped.
    """
    async with conn.conn.transaction():
        set_id = await conn.fetchval("SELECT set_id FROM upload_payment_quotes WHERE quote_id = $1", quote_id)
        competition = await lock_competition_for_admission(conn, set_id)
        if competition is None or competition.policy is None or competition.state is not CompetitionState.open:
            raise CompetitionNotAcceptingSubmissionsError(
                set_id=set_id, state=None if competition is None else competition.state.value
            )
        quote = await conn.fetchrow("SELECT * FROM upload_payment_quotes WHERE quote_id = $1 FOR UPDATE", quote_id)
        if quote["cancelled_at"] is not None:
            raise QuoteCancelledError()

        if quote["purchased_at"] is not None:
            return "already_purchased"

        if quote["amount_alpha_rao"] and quote["confirmed_at"] is None:
            raise QuoteNotConfirmedError()

        await lock_balance(conn, miner_coldkey)
        await lock_coldkey_ban_state(conn, miner_coldkey)
        if await get_banned_coldkey(miner_coldkey) is not None:
            raise ColdkeyBannedError(miner_coldkey)

        balance = await balance_alpha_rao(conn, miner_coldkey)
        price = await lock_competition_price(conn, set_id)
        price_alpha_rao = exact_alpha_rao_for_usd(price.price_usd, alpha_price_usd)
        if balance < price_alpha_rao:
            raise InsufficientBalanceError(
                price_usd=price.price_usd, price_alpha_rao=price_alpha_rao, balance_alpha_rao=balance
            )

        await conn.execute(
            "INSERT INTO burn_balance_entries (miner_coldkey, quote_id, kind, amount_alpha_rao) VALUES ($1, $2, 'purchase', $3)",
            miner_coldkey,
            quote_id,
            -price_alpha_rao,
        )
        # The quote now belongs to the coldkey that paid for it; redemption checks and records that coldkey.
        await conn.execute(
            """
            UPDATE upload_payment_quotes
            SET purchased_at = $2, purchase_price_usd = $3::float8, purchase_price_alpha_rao = $4, miner_coldkey = $5
            WHERE quote_id = $1
            """,
            quote_id,
            price.as_of,
            price.price_usd,
            price_alpha_rao,
            miner_coldkey,
        )
        await apply_bump(conn, set_id)
        return "purchased"
