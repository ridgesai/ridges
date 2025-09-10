from datetime import timezone
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
        INSERT INTO treasury_transactions (group_transaction_id, sender_coldkey, destination_coldkey, staker_hotkey, amount_alpha_rao, occurred_at, version_id, extrinsic_code, fee)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    """, transaction.group_transaction_id, transaction.sender_coldkey, transaction.destination_coldkey, transaction.staker_hotkey, transaction.amount_alpha, transaction.occurred_at, transaction.version_id, transaction.extrinsic_code, transaction.fee)

@db_operation
async def evaluate_agent_for_threshold_approval(conn: asyncpg.Connection, version_id: str, set_id: int) -> dict:
    """
    Evaluate an agent for threshold-based approval.
    
    Returns dict with:
    - action: 'approve_now' | 'approve_future' | 'reject'
    - future_approval_time: datetime if action is 'approve_future'
    - reason: explanation of the decision
    """
    from datetime import datetime, timedelta
    import math
    
    # Get agent's score
    agent_result = await conn.fetchrow("""
        SELECT final_score FROM agent_scores 
        WHERE version_id = $1 AND set_id = $2
    """, version_id, set_id)
    
    if not agent_result:
        return {"action": "reject", "reason": "Agent not found or no valid score"}
    
    agent_score = agent_result['final_score']
    
    # Get threshold function parameters
    threshold_data = await generate_threshold_function()
    current_time = datetime.now(timezone.utc)
    
    # Calculate current threshold value
    if threshold_data['epoch_0_time']:
        epoch_minutes = (current_time - threshold_data['epoch_0_time']).total_seconds() / 60
        epochs_passed = epoch_minutes / threshold_data['epoch_length_minutes']
    else:
        epochs_passed = 0
    
    # Parse threshold function: "floor + (t0 - floor) * Math.exp(-k * x)"
    threshold_func = threshold_data['threshold_function']
    parts = threshold_func.split(' + (')
    floor = float(parts[0])
    # Extract t0 (first number after opening parenthesis)
    t0_part = parts[1].split(' - ')[0]
    t0 = float(t0_part)
    k_part = parts[1].split('* Math.exp(-')[1].split(' * x)')[0]
    k = float(k_part)
    
    current_threshold = floor + (t0 - floor) * math.exp(-k * epochs_passed)
    
    # Get current top score (not necessarily approved), excluding the agent being evaluated
    top_score_result = await conn.fetchrow("""
        SELECT MAX(final_score) as top_score FROM agent_scores 
        WHERE set_id = $1 AND version_id != $2
    """, set_id, version_id)
    top_score = top_score_result['top_score'] if top_score_result else 0.0
    
    if agent_score >= current_threshold:
        return {
            "action": "approve_now",
            "reason": f"Score {agent_score:.4f} exceeds current threshold {current_threshold:.4f}"
        }
    elif agent_score > top_score:
        # Calculate when threshold will decay to agent's score
        if k == 0 or t0 <= floor:
            return {"action": "reject", "reason": "Threshold will never decay to agent score"}
        
        # Solve: agent_score = floor + (t0 - floor) * exp(-k * x)
        # x = -ln((agent_score - floor) / (t0 - floor)) / k
        if agent_score <= floor:
            return {"action": "reject", "reason": "Agent score below threshold floor"}
        
        ratio = (agent_score - floor) / (t0 - floor)
        if ratio <= 0:
            return {"action": "reject", "reason": "Invalid threshold calculation"}
        
        future_epochs = -math.log(ratio) / k
        future_minutes = future_epochs * threshold_data['epoch_length_minutes']
        future_approval_time = threshold_data['epoch_0_time'] + timedelta(minutes=future_minutes)
        
        return {
            "action": "approve_future", 
            "future_approval_time": future_approval_time,
            "reason": f"Score {agent_score:.4f} > top score {top_score:.4f} but < threshold {current_threshold:.4f}"
        }
    else:
        return {
            "action": "reject",
            "reason": f"Score {agent_score:.4f} not competitive (top: {top_score:.4f}, threshold: {current_threshold:.4f})"
        }



@db_operation
async def generate_threshold_function(conn: asyncpg.Connection) -> dict:
    """
    Build a JavaScript-compatible threshold function string using parameters from the DB
    Returns dict with threshold function and additional metadata
    """
    try:
        INNOVATION_WEIGHT = await conn.fetchval("SELECT value FROM threshold_config WHERE key = 'innovation_weight'")
        DECAY_PER_EPOCH = await conn.fetchval("SELECT value FROM threshold_config WHERE key = 'decay_per_epoch'")
        FRONTIER_WEIGHT = await conn.fetchval("SELECT value FROM threshold_config WHERE key = 'frontier_scale'")
        IMPROVEMENT_WEIGHT = await conn.fetchval("SELECT value FROM threshold_config WHERE key = 'improvement_weight'")
    except Exception:
        logger.error("Error fetching threshold config values from the database. Using default values.")
        INNOVATION_WEIGHT = 0.25
        DECAY_PER_EPOCH = 0.05
        FRONTIER_WEIGHT = 0.84
        IMPROVEMENT_WEIGHT = 0.30

    # Get the maximum set_id
    max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    max_set_id = max_set_id_result['max_set_id'] if max_set_id_result else 0

    # Get current top score (highest score overall in latest set)
    current_top_score_result = await conn.fetchrow("""
        SELECT MAX(final_score) as top_score
        FROM agent_scores
        WHERE set_id = $1
    """, max_set_id)
    current_top_score = float(current_top_score_result['top_score'] or 0.0)

    # Get current top approved score (highest score among approved agents in latest set)
    current_top_approved_score_result = await conn.fetchrow("""
        SELECT MAX(final_score) as top_approved_score
        FROM agent_scores
        WHERE set_id = $1 AND approved = true AND approved_at <= NOW()
    """, max_set_id)
    current_top_approved_score = float(current_top_approved_score_result['top_approved_score'] or 0.0)

    history_rows = await conn.fetch(
        """
        SELECT h.version_id, h.set_id, s.final_score
        FROM approved_top_agents_history h
        LEFT JOIN agent_scores s
          ON s.version_id = h.version_id AND s.set_id = h.set_id
        WHERE h.version_id IS NOT NULL
        ORDER BY h.top_at DESC
        LIMIT 2
        """
    )

    CURR_SCORE = 0.0
    PREV_SCORE = 0.0
    latest_version_id = None

    if len(history_rows) >= 1:
        latest_version_id = history_rows[0]["version_id"]
        CURR_SCORE = float(history_rows[0]["final_score"] or 0.0)
    if len(history_rows) >= 2:
        PREV_SCORE = float(history_rows[1]["final_score"] or 0.0)

    INNOVATION_SCALE = 0.0
    if latest_version_id is not None:
        innovation_row = await conn.fetchrow(
            "SELECT innovation FROM miner_agents WHERE version_id = $1",
            latest_version_id,
        )
        if innovation_row and innovation_row["innovation"] is not None:
            INNOVATION_SCALE = float(innovation_row["innovation"])

    # Handle case when there are no approved agents - set reasonable defaults
    if current_top_approved_score == 0.0 and len(history_rows) == 0:
        # No approved agents yet - set a high initial threshold
        floor = 0.0
        t0 = 0.85  # Start with a high threshold for first approvals
        k_effective = DECAY_PER_EPOCH
    else:
        delta = max(0.0, CURR_SCORE - PREV_SCORE)
        scaling_factor = 1.0 + FRONTIER_WEIGHT * PREV_SCORE
        threshold_boost = IMPROVEMENT_WEIGHT * delta * scaling_factor
        innovation_boost = max(0.0, INNOVATION_WEIGHT * INNOVATION_SCALE)
        t0 = min(1.0, max(0.0, CURR_SCORE + threshold_boost + innovation_boost))

        k_effective = DECAY_PER_EPOCH * max(0.1, 1.0 - FRONTIER_WEIGHT * CURR_SCORE)
        floor = CURR_SCORE

    # Get epoch 0 time (when the current top approved agent became approved)
    epoch_0_result = await conn.fetchrow("""
        SELECT avi.approved_at as epoch_0_time
        FROM agent_scores a
        JOIN approved_version_ids avi ON a.version_id = avi.version_id AND a.set_id = avi.set_id
        WHERE a.set_id = $1 AND a.approved = true AND avi.approved_at <= NOW()
        ORDER BY a.final_score DESC, a.created_at ASC
        LIMIT 1
    """, max_set_id)
    epoch_0_time = epoch_0_result['epoch_0_time'] if epoch_0_result else None

    # Epoch length in minutes 
    epoch_length_minutes = 72

    threshold_function = f"{floor:.6f} + ({t0:.6f} - {floor:.6f}) * Math.exp(-{k_effective:.6f} * x)"

    return {
        "threshold_function": threshold_function,
        "current_top_score": current_top_score,
        "current_top_approved_score": current_top_approved_score,
        "epoch_0_time": epoch_0_time,
        "epoch_length_minutes": epoch_length_minutes
    }
