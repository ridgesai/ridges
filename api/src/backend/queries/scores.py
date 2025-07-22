import logging
from uuid import UUID
import asyncpg
from api.src.backend.db_manager import db_operation


logger = logging.getLogger(__name__)

@db_operation
async def check_for_new_high_score(conn: asyncpg.Connection, version_id: UUID) -> dict:
    """
    Check if version_id scored higher than all approved agents within the same set_id.
    Uses dynamic scoring calculation from evaluations table, excluding the most outlier score.
    
    Returns dict with:
    - high_score_detected: bool
    - agent details if high score detected
    - reason if no high score detected
    """
    logger.debug(f"Attempting to get the current agent's details and computed score for version {version_id}.")
    
    # Get the current agent's details and computed score excluding most outlier
    agent_score_result = await conn.fetchrow("""
        WITH scores_with_outliers AS (
            SELECT 
                ma.agent_name, 
                ma.miner_hotkey, 
                ma.version_num,
                e.set_id,
                e.score,
                e.validator_hotkey,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY e.score) OVER (PARTITION BY ma.version_id) AS median_score,
                ABS(e.score - PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY e.score) OVER (PARTITION BY ma.version_id)) AS deviation
            FROM miner_agents ma
            JOIN evaluations e ON ma.version_id = e.version_id
            WHERE ma.version_id = $1 
              AND e.status = 'completed'
              AND e.score IS NOT NULL
              AND e.score > 0
              AND e.validator_hotkey NOT LIKE 'i-0%'
        ),
        max_outlier AS (
            SELECT MAX(deviation) AS max_deviation
            FROM scores_with_outliers
        )
        SELECT 
            agent_name, 
            miner_hotkey, 
            version_num,
            set_id,
            AVG(score) AS computed_score,
            COUNT(DISTINCT validator_hotkey) AS num_validators
        FROM scores_with_outliers swo
        LEFT JOIN max_outlier mo ON swo.deviation = mo.max_deviation
        WHERE mo.max_deviation IS NULL  -- Exclude the most outlier score
        GROUP BY agent_name, miner_hotkey, version_num, set_id
        HAVING COUNT(DISTINCT validator_hotkey) >= 2  -- At least 2 validator evaluations
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
    
    # Get the highest score among ALL approved agents within the same set_id excluding outliers
    logger.debug(f"Attempting to get the highest score among ALL approved agents for set_id {current_set_id}.")
    max_approved_result = await conn.fetchrow("""
        WITH scores_with_outliers AS (
            SELECT 
                avi.version_id,
                e.score,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY e.score) OVER (PARTITION BY avi.version_id) AS median_score,
                ABS(e.score - PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY e.score) OVER (PARTITION BY avi.version_id)) AS deviation
            FROM approved_version_ids avi
            JOIN evaluations e ON avi.version_id = e.version_id  
            WHERE e.status = 'completed' 
              AND e.score IS NOT NULL
              AND e.score > 0
              AND e.set_id = $1
              AND e.validator_hotkey NOT LIKE 'i-0%'
        ),
        version_outliers AS (
            SELECT version_id, MAX(deviation) AS max_deviation
            FROM scores_with_outliers
            GROUP BY version_id
        )
        SELECT 
            AVG(swo.score) as computed_score
        FROM scores_with_outliers swo
        LEFT JOIN version_outliers vo ON swo.version_id = vo.version_id 
            AND swo.deviation = vo.max_deviation
        WHERE vo.max_deviation IS NULL  -- Exclude most outlier score per version
        GROUP BY swo.version_id
        HAVING COUNT(DISTINCT swo.score) >= 2  -- Need at least 2 scores after outlier removal
        ORDER BY AVG(swo.score) DESC
        LIMIT 1
    """, current_set_id)
    
    max_approved_score = max_approved_result['computed_score'] if max_approved_result else None
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
