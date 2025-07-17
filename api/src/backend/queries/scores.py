import logging
from uuid import UUID
import asyncpg
from api.src.backend.db_manager import db_operation


logger = logging.getLogger(__name__)

@db_operation
async def check_for_new_high_score(conn: asyncpg.Connection, version_id: UUID) -> dict:
    """
    Check if version_id scored higher than all approved agents within the same set_id.
    Uses dynamic scoring calculation from evaluations table.
    
    Returns dict with:
    - high_score_detected: bool
    - agent details if high score detected
    - reason if no high score detected
    """
    logger.debug(f"Attempting to get the current agent's details and computed score for version {version_id}.")
    
    # Get the current agent's details and computed score from evaluations
    agent_score_result = await conn.fetchrow("""
        SELECT 
            ma.agent_name, 
            ma.miner_hotkey, 
            ma.version_num,
            e.set_id,
            AVG(e.score) AS computed_score,
            COUNT(DISTINCT e.validator_hotkey) AS num_validators
        FROM miner_agents ma
        JOIN evaluations e ON ma.version_id = e.version_id
        WHERE ma.version_id = $1 
          AND e.status = 'completed'
          AND e.score IS NOT NULL
          AND e.score > 0
          AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
        GROUP BY ma.agent_name, ma.miner_hotkey, ma.version_num, e.set_id
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
    """, version_id)
    
    if not agent_score_result:
        logger.debug(f"No agent found or no valid score available for version {version_id}.")
        return {
            "high_score_detected": False, 
            "reason": "Agent not found or no valid score available (need 2+ validators)"
        }
    
    current_score = agent_score_result['computed_score']
    current_set_id = agent_score_result['set_id']
    logger.debug(f"Current agent's computed score for version {version_id} is {current_score} on set_id {current_set_id}.")
    
    # Get the highest score among ALL approved agents within the same set_id
    logger.debug(f"Attempting to get the highest score among ALL approved agents for set_id {current_set_id}.")
    max_approved_result = await conn.fetchrow("""
        SELECT MAX(avg_scores.computed_score) as max_approved_score
        FROM (
            SELECT AVG(e.score) AS computed_score
            FROM approved_version_ids avi
            JOIN evaluations e ON avi.version_id = e.version_id  
            WHERE e.status = 'completed' 
              AND e.score IS NOT NULL
              AND e.score > 0
              AND e.set_id = $1
              AND e.validator_hotkey NOT LIKE 'i-0%'  -- Exclude screener scores
            GROUP BY e.version_id
            HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- At least 2 validator evaluations
        ) avg_scores
    """, current_set_id)
    
    max_approved_score = max_approved_result['max_approved_score'] if max_approved_result else None
    logger.debug(f"The highest score among ALL approved agents for set_id {current_set_id} is {max_approved_score}.")
    
    # Check if this beats all approved agents (ANY improvement triggers notification)
    if max_approved_score is None or current_score > max_approved_score:
        logger.info(f"🎯 HIGH SCORE DETECTED: {agent_score_result['agent_name']} scored {current_score:.4f} vs previous max {max_approved_score or 0.0:.4f} on set_id {current_set_id}")
        return {
            "high_score_detected": True,
            "agent_name": agent_score_result['agent_name'],
            "miner_hotkey": agent_score_result['miner_hotkey'], 
            "version_id": str(version_id),
            "version_num": agent_score_result['version_num'],
            "new_score": float(current_score),
            "previous_max_score": float(max_approved_score) if max_approved_score else 0.0,
            "set_id": current_set_id
        }

    return {
        "high_score_detected": False,
        "reason": f"Score {current_score:.4f} does not beat max approved score {max_approved_score:.4f} on set_id {current_set_id}"
    }
