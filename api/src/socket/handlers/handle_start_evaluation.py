import json
from datetime import datetime
from typing import Dict, Any
from fastapi import WebSocket

from ...utils.logging_utils import get_logger
from ...db.operations import DatabaseManager

logger = get_logger(__name__)

db = DatabaseManager()

async def handle_start_evaluation(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle start-evaluation message from a validator"""
    evaluation_id = response_json["evaluation_id"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} has started an evaluation {evaluation_id}. Attempting to update the evaluation in the database.")
    
    try:
        evaluation = await db.get_evaluation(evaluation_id)
        evaluation.status = "running"
        evaluation.started_at = datetime.now()
        await db.store_evaluation(evaluation)
        
        return evaluation
        
    except Exception as e:
        logger.error(f"Error starting evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to start evaluation: {str(e)}"} 