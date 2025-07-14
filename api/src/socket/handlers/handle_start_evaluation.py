from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import WebSocket

from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, store_evaluation
from api.src.backend.entities import EvaluationStatus
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
        logger.debug(f"Attempting to get evaluation {evaluation_id} from the database.")
        evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
        logger.debug(f"Evaluation {evaluation_id} found in the database.")
        evaluation.status = EvaluationStatus.running
        evaluation.started_at = datetime.now(timezone.utc)
        logger.debug(f"Attempting to update the evaluation {evaluation_id} in the database with the following new details, status: {evaluation.status}, started_at: {evaluation.started_at}.")
        await store_evaluation(evaluation)
        logger.debug(f"Successfully updated the evaluation {evaluation_id} in the database.")
        
        return evaluation
        
    except Exception as e:
        logger.error(f"Error starting evaluation {evaluation_id}: {str(e)}")
        return {"error": f"Failed to start evaluation: {str(e)}"} 