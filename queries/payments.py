from typing import Optional
from uuid import UUID

from models.payments import Payment
from utils.database import db_operation, DatabaseConnection


@db_operation
async def record_evaluation_payment(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str, 
    amount_rao: int,
    agent_id: UUID,
    miner_hotkey: str,
    miner_coldkey: str
):
    await conn.execute("""
        INSERT INTO evaluation_payments (
            payment_block_hash,
            payment_extrinsic_index,

            agent_id,

            miner_hotkey,
            miner_coldkey,

            amount_rao
        ) VALUES ($1, $2, $3, $4, $5, $6)
    """, payment_block_hash, payment_extrinsic_index, str(agent_id), miner_hotkey, miner_coldkey, amount_rao)

@db_operation
async def retrieve_payment_by_hash(
    conn: DatabaseConnection,
    payment_block_hash: str,
    payment_extrinsic_index: str,
) -> Optional[Payment]:
    result = await conn.fetchrow("""
        select * from agents 
        where payment_block_hash = $1
        and payment_extrinsic_index = $2
        order by created_at desc
        limit 1
    """, payment_block_hash, payment_extrinsic_index)

    if result is None:
        return None 
    
    return Payment(**result)