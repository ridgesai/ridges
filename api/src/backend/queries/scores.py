import logging
from uuid import UUID
import asyncpg
from api.src.backend.db_manager import db_operation


logger = logging.getLogger(__name__)

@db_operation
async def check_for_new_high_score(conn: asyncpg.Connection, version_id: UUID) -> dict:
    """
    Check if version_id scored higher than all approved agents.
    Uses LEFT JOIN to compare against approved_version_ids scores.
    
    Returns dict with:
    - high_score_detected: bool
    - agent details if high score detected
    - reason if no high score detected
    """
    logger.debug(f"Attempting to get the current agent's details and score from miner_agents for version {version_id}.")
    # Get the current agent's details and score from miner_agents
    agent_result = await conn.fetchrow("""
        SELECT agent_name, miner_hotkey, version_num, score
        FROM miner_agents 
        WHERE version_id = $1 AND score IS NOT NULL
    """, version_id)
    logger.debug(f"Successfully retrieved the current agent's details and score from miner_agents for version {version_id}.")
    
    if not agent_result:
        logger.debug(f"No agent found or no score available for version {version_id}.")
        return {
            "high_score_detected": False, 
            "reason": "Agent not found or no score available"
        }
    
    current_score = agent_result['score']
    logger.debug(f"Current agent's score for version {version_id} is {current_score}.")
    
    # Get the highest score among ALL approved agents using LEFT JOIN
    logger.debug(f"Attempting to get the highest score among ALL approved agents using LEFT JOIN.")
    max_approved_result = await conn.fetchrow("""
        SELECT MAX(e.score) as max_approved_score
        FROM approved_version_ids avi
        LEFT JOIN evaluations e ON avi.version_id = e.version_id  
        WHERE e.status = 'completed' AND e.score IS NOT NULL
    """)
    
    max_approved_score = max_approved_result['max_approved_score'] if max_approved_result else None
    logger.debug(f"The highest score among ALL approved agents is {max_approved_score}.")
    
    # Check if this beats all approved agents (ANY improvement triggers notification)
    if max_approved_score is None or current_score > max_approved_score:
        logger.info(f"🎯 HIGH SCORE DETECTED: {agent_result['agent_name']} scored {current_score:.4f} vs previous max {max_approved_score or 0.0:.4f}")
        return {
            "high_score_detected": True,
            "agent_name": agent_result['agent_name'],
            "miner_hotkey": agent_result['miner_hotkey'], 
            "version_id": str(version_id),
            "version_num": agent_result['version_num'],
            "new_score": current_score,
            "previous_max_score": max_approved_score or 0.0
        }

    return {
        "high_score_detected": False,
        "reason": f"Score {current_score:.4f} does not beat max approved score {max_approved_score:.4f}"
    }
