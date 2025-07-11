from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, store_evaluation, check_for_new_high_score
from api.src.backend.entities import EvaluationStatus
from api.src.backend.queries.agents import set_agent_status
from api.src.utils.logging_utils import get_logger
from api.src.utils.slack import send_high_score_notification
from api.src.socket.websocket_manager import WebSocketManager

logger = get_logger(__name__)
ws = WebSocketManager.get_instance()

async def handle_finish_evaluation(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle finish-evaluation message from a validator"""
    
    evaluation_id = response_json["evaluation_id"]
    errored = response_json["errored"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} has informed the platform that it has finished an evaluation {evaluation_id}. Attempting to update the evaluation in the database.")
    
    try:
        evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
        evaluation.status = EvaluationStatus.completed if not errored else EvaluationStatus.error
        evaluation.finished_at = datetime.now(timezone.utc)
        # Set score to None to preserve the trigger-calculated score
        evaluation.score = None
        logger.debug(f"Attempting to update evaluation {evaluation_id} in the database with the following new details, status: {evaluation.status}, finished_at: {evaluation.finished_at}, score: {evaluation.score}.")
        await store_evaluation(evaluation)

        if evaluation.validator_hotkey.startswith("i-0"):
            logger.debug(f"Evaluation {evaluation_id} is from a screener. Attempting to update the agent status.")
            evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
            if evaluation.score is not None and evaluation.score >= 0.8:
                logger.debug(f"Evaluation {evaluation_id} has a score of {evaluation.score}, meaning they passed the screener. Attempting to create new evaluations.")
                await ws.create_new_evaluations(evaluation.version_id)
                logger.debug(f"Successfully created new evaluations.")

                logger.debug(f"Attempting to update the agent status to waiting.")
                await set_agent_status(evaluation.version_id, "waiting")
                logger.debug(f"Successfully updated the agent status to waiting.")
            else:
                logger.debug(f"Evaluation {evaluation_id} has a score of {evaluation.score}. There will be no new evaluations created. Attempting to update the agent status to scored.")
                await set_agent_status(evaluation.version_id, "scored")
        
        # 🆕 NEW: Check for high score after evaluation completes successfully
        if not errored:  # Only check if evaluation completed successfully
            logger.debug(f"Evaluation {evaluation_id} completed successfully. Attempting to check for high score.")
            try:
                high_score_result = await check_for_new_high_score(evaluation.version_id)
                if high_score_result["high_score_detected"]:
                    # Send Slack notification for manual approval
                    logger.debug(f"High score detected for {evaluation.version_id}. Attempting to send Slack notification.")
                    await send_high_score_notification(
                        agent_name=high_score_result["agent_name"],
                        miner_hotkey=high_score_result["miner_hotkey"],
                        version_id=high_score_result["version_id"],
                        version_num=high_score_result["version_num"],
                        new_score=high_score_result["new_score"],
                        previous_score=high_score_result["previous_max_score"]
                    )
                    logger.info(f"🎯 HIGH SCORE NOTIFICATION SENT: {high_score_result['agent_name']} scored {high_score_result['new_score']:.4f}")
                else:
                    logger.debug(f"No high score detected for {evaluation.version_id}: {high_score_result['reason']}")
            except Exception as e:
                logger.error(f"Error checking for high score on evaluation {evaluation_id}: {e}")
                # Don't let high score detection errors break the evaluation flow
        
        return evaluation
        
    except Exception as e:
        logger.error(f"Error finishing evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to finish evaluation: {str(e)}"} 