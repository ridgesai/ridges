import json
from datetime import datetime
from typing import Dict, Any
from fastapi import WebSocket

from ...utils.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_finish_evaluation(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle finish-evaluation message from a validator"""
    
    evaluation_id = response_json["evaluation_id"]
    errored = response_json["errored"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} has finished an evaluation {evaluation_id}. Attempting to update the evaluation in the database.")
    
    try:
        evaluation = await get_evaluation(evaluation_id)
        evaluation.status = "completed" if not errored else "error"
        evaluation.finished_at = datetime.now()
        await db.store_evaluation(evaluation)
        
        return evaluation
        
    except Exception as e:
        logger.error(f"Error finishing evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to finish evaluation: {str(e)}"} 