from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import start_evaluation
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
        evaluation = await start_evaluation(evaluation_id, screener=validator_hotkey.startswith("i-0"))
        logger.debug(f"Successfully started evaluation {evaluation_id}.")
        return evaluation
    except Exception as e:
        logger.error(f"Error starting evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to start evaluation: {str(e)}"} 