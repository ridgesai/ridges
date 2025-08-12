import logging
from uuid import UUID
import asyncpg
from api.src.backend.db_manager import db_operation
from api.src.backend.entities import MinerAgentScored, TreasuryTransaction


logger = logging.getLogger(__name__)

@db_operation
async def check_for_new_high_score(conn: asyncpg.Connection, version_id: UUID) -> dict:
    """
    Check if version_id scored higher than all approved agents within the same set_id.
    Uses the agent_scores materialized view for performance.
    
    Returns dict with:
    - high_score_detected: bool
    - agent details if high score detected
    - reason if no high score detected
    """
    logger.debug(f"Checking for new high score for version {version_id} using agent_scores materialized view.")
    
    result = await MinerAgentScored.check_for_new_high_score(conn, version_id)
    
    if result["high_score_detected"]:
        logger.info(f"🎯 HIGH SCORE DETECTED: {result['agent_name']} scored {result['new_score']:.4f} vs previous max {result['previous_max_score']:.4f} on set_id {result['set_id']}")
    else:
        logger.debug(f"No high score detected for version {version_id}: {result['reason']}")
    
    return result

@db_operation
async def get_treasury_hotkeys(conn: asyncpg.Connection) -> list[str]:
    """
    Returns a list of all treasury hotkeys
    """
    rows = await conn.fetch("""
        SELECT hotkey FROM treasury_wallets WHERE active = TRUE
    """)
    return [r["hotkey"] for r in rows]

@db_operation
async def store_treasury_transaction(conn: asyncpg.Connection, transaction: TreasuryTransaction):
    """
    Stores a treasury transaction in the database.
    """
    await conn.execute("""
        INSERT INTO treasury_transactions (sender_coldkey, destination_coldkey, staker_hotkey, amount_rao, fee_rao, occured_at, version_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """, transaction.sender_coldkey, transaction.destination_coldkey, transaction.staker_hotkey, transaction.amount_rao, transaction.fee_rao, transaction.occured_at, transaction.version_id)
