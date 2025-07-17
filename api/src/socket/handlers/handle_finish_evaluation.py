
import json
from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id
from api.src.backend.queries.scores import check_for_new_high_score
from api.src.backend.queries.agents import get_agent_by_version_id
from api.src.backend.state_machine import state_machine
from loggers.logging_utils import get_logger
from api.src.utils.slack import send_high_score_notification
from api.src.utils.config import SCREENING_THRESHOLD

logger = get_logger(__name__)

async def handle_finish_evaluation(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle finish-evaluation message from a validator.
    This uses the state machine to ensure proper state transitions.
    """
    
    evaluation_id = response_json["evaluation_id"]
    errored = response_json["errored"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} has informed the platform that it has finished an evaluation {evaluation_id}. Attempting to update the evaluation in the database.")
    
    try:
        # Get evaluation details before finishing
        evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
        is_screener = evaluation.validator_hotkey.startswith("i-0")
        
        # Extract score from response if available
        score = response_json.get("score", 0.0)
        
        # Use state machine to finish evaluation - this handles all state transitions
        if is_screener:
            success = await state_machine.finish_screening(evaluation_id, score)
        else:
            success = await state_machine.finish_evaluation(evaluation_id, score, errored)
        
        if not success:
            logger.warning(f"Failed to finish evaluation {evaluation_id} for validator {validator_hotkey}")
            return {"error": "Failed to finish evaluation"}
        
        # For screeners, check if there's another evaluation waiting
        if is_screener:
            from api.src.socket.websocket_manager import WebSocketManager
            ws = WebSocketManager.get_instance()
            
            # Try to find another evaluation for this screener
            next_evaluation = await state_machine.get_next_screening_evaluation(validator_hotkey)
            if next_evaluation:
                logger.info(f"Sending screener {validator_hotkey} evaluation {next_evaluation.evaluation_id}")
                miner_agent = await get_agent_by_version_id(next_evaluation.version_id)
                await websocket.send_text(json.dumps({
                    "event": "screen-agent",
                    "evaluation_id": str(next_evaluation.evaluation_id),
                    "agent_version": miner_agent.model_dump(mode='json')
                }))
                logger.info(f"Successfully sent evaluation {next_evaluation.evaluation_id} to validator {validator_hotkey}")
            else:
                # Mark screener as available
                if websocket in ws.clients:
                    ws.clients[websocket].status = "available"
        
        # Check for high score after evaluation completes successfully
        if not errored:
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