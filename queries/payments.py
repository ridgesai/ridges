from datetime import datetime
from typing import Optional
from uuid import UUID

from models.payments import Payment, PaymentQuote
from utils.database import DatabaseConnection, db_operation


@db_operation
async def reserve_payment(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str,
    miner_hotkey: str,
    miner_coldkey: str,
    amount_rao: int,
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
    amount_rao : int
        Amount of RAO associated with the payment.
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
            amount_rao,
            quote_id
        ) VALUES ($1, $2, NULL, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        payment_block_hash,
        payment_extrinsic_index,
        miner_hotkey,
        miner_coldkey,
        amount_rao,
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
async def create_payment_quote(
    conn: DatabaseConnection,
    miner_hotkey: str,
    amount_rao: int,
    send_address: str,
    expires_at: datetime,
) -> PaymentQuote:
    result = await conn.fetchrow(
        """
        INSERT INTO upload_payment_quotes (
            miner_hotkey,
            amount_rao,
            send_address,
            expires_at
        ) VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        miner_hotkey,
        amount_rao,
        send_address,
        expires_at,
    )
    return PaymentQuote(**result)


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
