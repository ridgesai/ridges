from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from uuid import UUID

from models.competition import CompetitionState
from models.payments import Payment, PaymentQuote
from queries.burn_balance import balance_alpha_rao, credit_burn
from queries.competition import lock_competition_for_admission
from queries.errors import (
    BurnNotReportedError,
    CompetitionNotAcceptingSubmissionsError,
    InsufficientAlphaError,
    OpenQuoteExistsError,
    QuoteAlreadyConfirmedError,
    QuoteAlreadyPurchasedError,
    QuoteCancelledError,
    ReceiptConflictError,
)
from queries.upload_price import lock_competition_price
from utils.database import DatabaseConnection, db_operation
from utils.upload_pricing import ALPHA_BUFFER, exact_alpha_rao_for_usd


@db_operation
async def reserve_payment(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str,
    miner_hotkey: str,
    miner_coldkey: str,
    amount_alpha_rao: int,
    quote_id: Optional[UUID] = None,
) -> Optional[Payment]:
    """Reserve a payment for an upload agent operation. It creates a new payment record with the given details, but with a NULL agent_id or it retrieves an existing payment row. The payment is considered reserved until the agent_id is set, which happens when the upload is completed.

    Parameters
    ----------
    conn : DatabaseConnection
        Database connection to use for the operation.
    payment_block_hash : str
        Hash of the block containing the payment extrinsic.
    payment_extrinsic_index : str
        Index of the payment extrinsic within the block.
    miner_hotkey : str
        Hotkey of the miner.
    miner_coldkey : str
        Coldkey of the miner.
    amount_alpha_rao : int
        Amount of SN62 alpha (1e9 units) burned.
    quote_id : Optional[UUID], optional
        Server-issued upload payment quote used to validate the payment.

    Returns
    -------
    Optional[Payment]
        Payment row corresponding to the reserved payment. If a payment with the same block hash and extrinsic index already exists, it returns that payment instead of creating a new one.
    """
    await conn.execute(
        """
        INSERT INTO evaluation_payments (
            payment_block_hash,
            payment_extrinsic_index,
            agent_id,
            miner_hotkey,
            miner_coldkey,
            amount_alpha_rao,
            quote_id
        ) VALUES ($1, $2, NULL, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        payment_block_hash,
        payment_extrinsic_index,
        miner_hotkey,
        miner_coldkey,
        amount_alpha_rao,
        quote_id,
    )
    return await retrieve_payment_by_hash(
        payment_block_hash=payment_block_hash,
        payment_extrinsic_index=payment_extrinsic_index,
    )


@db_operation
async def complete_payment(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str,
    agent_id: UUID,
) -> None:
    """Complete a reserved payment by associating it with an agent. This function updates the payment record that matches the given block hash and extrinsic index, setting its agent_id to the provided agent_id.

    It only updates records where agent_id is currently NULL, ensuring that only reserved payments can be completed.

    Parameters
    ----------
    conn : DatabaseConnection
        The database connection to use for the operation.
    payment_block_hash : str
        Hash of the block containing the payment extrinsic.
    payment_extrinsic_index : str
        Index of the payment extrinsic within the block.
    agent_id : UUID
        The UUID of the agent to associate with the payment, marking it as completed.
    """
    await conn.execute(
        """
        UPDATE evaluation_payments
        SET agent_id = $3
        WHERE payment_block_hash = $1
          AND payment_extrinsic_index = $2
          AND agent_id IS NULL
        """,
        payment_block_hash,
        payment_extrinsic_index,
        str(agent_id),
    )


@db_operation
async def retrieve_payment_by_hash(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str,
) -> Optional[Payment]:
    result = await conn.fetchrow(
        """
        select * from evaluation_payments
        where payment_block_hash = $1
        and payment_extrinsic_index = $2
        order by created_at desc
        limit 1
    """,
        payment_block_hash,
        payment_extrinsic_index,
    )

    if result is None:
        return None

    return Payment(**result)


@db_operation
async def retrieve_payment_quote(
    conn: DatabaseConnection,
    quote_id: UUID,
) -> Optional[PaymentQuote]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM upload_payment_quotes
        WHERE quote_id = $1
        """,
        quote_id,
    )

    if result is None:
        return None

    return PaymentQuote(**result)


@dataclass(frozen=True, slots=True)
class IssuedQuote:
    quote: PaymentQuote
    upload_price_usd: float
    balance_alpha_rao: int


@db_operation
async def issue_competition_quote(
    conn: DatabaseConnection,
    *,
    set_id: int,
    miner_hotkey: str,
    miner_coldkey: str,
    alpha_price_usd: float,
    burnable_rao: int,
    ttl_seconds: int,
) -> IssuedQuote:
    """Issue a quote for the gap between the coldkey's burn balance and the competition's current price in alpha.

    The gap carries the 1.1 buffer; whatever is not spent at purchase stays in the balance. A gap of zero yields
    a quote with nothing to burn. One open quote per coldkey per competition: the same hotkey gets its open quote
    back, another hotkey of the same coldkey gets OpenQuoteExistsError.
    """
    async with conn.conn.transaction():
        competition = await lock_competition_for_admission(conn, set_id)
        if competition is None or competition.policy is None or competition.state is not CompetitionState.open:
            raise CompetitionNotAcceptingSubmissionsError(
                set_id=set_id, state=None if competition is None else competition.state.value
            )

        price = await lock_competition_price(conn, set_id)
        open_quote = await conn.fetchrow(
            """
            SELECT *
            FROM upload_payment_quotes
            WHERE set_id = $1
              AND miner_coldkey = $2
              AND NOT is_legacy
              AND confirmed_at IS NULL
              AND purchased_at IS NULL
              AND cancelled_at IS NULL
              AND expires_at > $3
            ORDER BY created_at DESC
            LIMIT 1
            """,
            set_id,
            miner_coldkey,
            price.as_of,
        )

        balance = await balance_alpha_rao(conn, miner_coldkey)
        if open_quote is not None:
            if open_quote["miner_hotkey"] == miner_hotkey:
                return IssuedQuote(PaymentQuote(**open_quote), price.price_usd, balance)
            raise OpenQuoteExistsError(quote_id=open_quote["quote_id"], expires_at=open_quote["expires_at"])

        gap_rao = max(0, exact_alpha_rao_for_usd(price.price_usd, alpha_price_usd) - balance)
        amount_alpha_rao = int(gap_rao * ALPHA_BUFFER) if gap_rao > 0 else 0
        if amount_alpha_rao > burnable_rao:
            raise InsufficientAlphaError(amount_alpha_rao)

        row = await conn.fetchrow(
            """
            INSERT INTO upload_payment_quotes (
                miner_hotkey, miner_coldkey, set_id, price_usd, amount_alpha_rao, created_at, expires_at
            ) VALUES ($1, $2, $3, $4::float8, $5, $6, $7)
            RETURNING *
            """,
            miner_hotkey,
            miner_coldkey,
            set_id,
            price.price_usd,
            amount_alpha_rao,
            price.as_of,
            price.as_of + timedelta(seconds=ttl_seconds),
        )
        return IssuedQuote(PaymentQuote(**row), price.price_usd, balance)


@db_operation
async def confirm_quote_payment(
    conn: DatabaseConnection,
    *,
    quote_id: UUID,
    payment_block_hash: str,
    payment_extrinsic_index: str,
    miner_hotkey: str,
    miner_coldkey: Optional[str],
    amount_alpha_rao: Optional[int],
    grace_seconds: int,
) -> str:
    """Record a verified burn against a competition-bound quote and bump the price once."""
    async with conn.conn.transaction():
        quote = await conn.fetchrow("SELECT * FROM upload_payment_quotes WHERE quote_id = $1 FOR UPDATE", quote_id)
        if quote["cancelled_at"] is not None:
            raise QuoteCancelledError()

        if quote["confirmed_at"] is not None:
            receipt = (quote["confirmed_payment_block_hash"], quote["confirmed_payment_extrinsic_index"])
            if receipt == (payment_block_hash, payment_extrinsic_index):
                return "replayed"
            raise ReceiptConflictError()

        now = await conn.fetchval("SELECT clock_timestamp()")
        if now > quote["expires_at"] + timedelta(seconds=grace_seconds):
            raise BurnNotReportedError()

        if miner_coldkey is not None:
            await conn.execute(
                """
                INSERT INTO evaluation_payments (
                    payment_block_hash, payment_extrinsic_index, agent_id, miner_hotkey, miner_coldkey,
                    amount_alpha_rao, quote_id
                ) VALUES ($1, $2, NULL, $3, $4, $5, $6)
                ON CONFLICT DO NOTHING
                """,
                payment_block_hash,
                payment_extrinsic_index,
                miner_hotkey,
                miner_coldkey,
                amount_alpha_rao,
                quote_id,
            )
            payment = await conn.fetchrow(
                "SELECT quote_id FROM evaluation_payments "
                "WHERE payment_block_hash = $1 AND payment_extrinsic_index = $2 FOR UPDATE",
                payment_block_hash,
                payment_extrinsic_index,
            )
            if payment is None or payment["quote_id"] != quote_id:
                raise ReceiptConflictError()

        await conn.execute(
            """
            UPDATE upload_payment_quotes
            SET confirmed_at = $2, confirmed_payment_block_hash = $3, confirmed_payment_extrinsic_index = $4
            WHERE quote_id = $1
            """,
            quote_id,
            now,
            payment_block_hash,
            payment_extrinsic_index,
        )
        burned_rao = quote["amount_alpha_rao"] if amount_alpha_rao is None else amount_alpha_rao
        await credit_burn(
            conn,
            quote_id=quote_id,
            miner_coldkey=quote["miner_coldkey"] if miner_coldkey is None else miner_coldkey,
            amount_alpha_rao=burned_rao,
        )
        return "confirmed"


@db_operation
async def cancel_payment_quote(conn: DatabaseConnection, quote_id: UUID) -> None:
    """Release an unburned quote. A cancelled quote can never be confirmed. Idempotent."""
    async with conn.conn.transaction():
        quote = await conn.fetchrow(
            "SELECT confirmed_at, purchased_at FROM upload_payment_quotes WHERE quote_id = $1 FOR UPDATE", quote_id
        )
        if quote["purchased_at"] is not None:
            raise QuoteAlreadyPurchasedError()
        if quote["confirmed_at"] is not None:
            raise QuoteAlreadyConfirmedError()

        await conn.execute(
            "UPDATE upload_payment_quotes SET cancelled_at = COALESCE(cancelled_at, clock_timestamp()) "
            "WHERE quote_id = $1",
            quote_id,
        )
