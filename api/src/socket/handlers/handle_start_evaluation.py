from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.state_machine import state_machine
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_start_evaluation(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle start-evaluation message from a validator"""
    evaluation_id = response_json["evaluation_id"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} has started an evaluation {evaluation_id}. Attempting to update the evaluation in the database.")
    
    try:
        is_screener = validator_hotkey.startswith("i-0")
        if is_screener:
            success = await state_machine.start_screening(evaluation_id, validator_hotkey)
        else:
            success = await state_machine.start_evaluation(evaluation_id, validator_hotkey)
        
        if success:
            logger.debug(f"Successfully started evaluation {evaluation_id}.")
            return {"evaluation_id": evaluation_id, "status": "started"}
        else:
            logger.warning(f"Failed to start evaluation {evaluation_id}.")
            return {"error": "Failed to start evaluation"}
    except Exception as e:
        logger.error(f"Error starting evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to start evaluation: {str(e)}"} 