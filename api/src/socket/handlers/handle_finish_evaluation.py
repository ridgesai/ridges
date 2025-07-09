from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, store_evaluation
from api.src.backend.entities import EvaluationStatus
from api.src.utils.logging_utils import get_logger

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
        evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
        evaluation.status = EvaluationStatus.completed if not errored else EvaluationStatus.error
        evaluation.finished_at = datetime.now(timezone.utc)
        await store_evaluation(evaluation)
        
        return evaluation
        
    except Exception as e:
        logger.error(f"Error finishing evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to finish evaluation: {str(e)}"} 