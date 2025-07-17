
import json
from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import create_next_evaluation_for_screener, finish_evaluation, get_evaluation_by_evaluation_id
from api.src.backend.queries.scores import check_for_new_high_score
from api.src.backend.queries.agents import get_agent_by_version_id, set_agent_status
from loggers.logging_utils import get_logger
from api.src.utils.slack import send_high_score_notification
from api.src.utils.config import SCREENING_THRESHOLD

logger = get_logger(__name__)

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
        evaluation = await finish_evaluation(evaluation_id, errored)
        is_screener = evaluation.validator_hotkey.startswith("i-0")

        if is_screener:
            from api.src.socket.websocket_manager import WebSocketManager
            ws = WebSocketManager.get_instance()

            logger.debug(f"Evaluation {evaluation_id} is from a screener. Attempting to update the agent status.")
            evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
            if evaluation.score is not None and evaluation.score >= SCREENING_THRESHOLD:
                logger.debug(f"Evaluation {evaluation_id} has a score of {evaluation.score}, meaning they passed the screener. Attempting to create new evaluations.")
                await ws.create_new_evaluations(evaluation.version_id)
                logger.debug(f"Successfully created new evaluations.")

                logger.debug(f"Attempting to update the agent status to waiting.")
                await set_agent_status(evaluation.version_id, "waiting")
                logger.debug(f"Successfully updated the agent status to waiting.")
            else:
                logger.debug(f"Evaluation {evaluation_id} failed screening with a score of {evaluation.score}. There will be no new evaluations created. Attempting to update the agent status to scored.")
                await set_agent_status(evaluation.version_id, "scored")

            evaluation = await create_next_evaluation_for_screener(validator_hotkey)
            if evaluation:
                logger.info(f"Sending screener {validator_hotkey} evaluation {evaluation.evaluation_id} to screener {validator_hotkey}")
                miner_agent = await get_agent_by_version_id(evaluation.version_id)
                await websocket.send_text(json.dumps({
                    "event": "screen-agent",
                    "evaluation_id": str(evaluation.evaluation_id),
                    "agent_version": miner_agent.model_dump(mode='json')
                }))
                logger.info(f"Successfully sent evaluation {evaluation.evaluation_id} to validator {validator_hotkey}")
            else:
                ws.clients[websocket].status = "available" # Mark the screener as available for next screening
        
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