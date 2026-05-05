from utils.database import DatabaseConnection, db_operation


@db_operation
async def is_payment_refunded(
    conn: DatabaseConnection,
    upload_block_hash: str,
) -> bool:
    """Check if a payment with a certain
    block hash has already been refunded.

    Parameters
    ----------
    conn : DatabaseConnection
        Database connection to use for the operation.
    upload_block_hash : str
        The block hash of the payment to check.

    Returns
    -------
    bool
        True if the payment has been refunded, False otherwise.
    """
    result = await conn.fetchrow(
        """
        SELECT 1 FROM failed_upload_refunds
        WHERE upload_block_hash = $1
        LIMIT 1
        """,
        upload_block_hash,
    )
    return result is not None
