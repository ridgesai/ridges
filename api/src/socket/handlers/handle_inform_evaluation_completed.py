from typing import TYPE_CHECKING, Dict, Any
from datetime import datetime

from api.src.backend.entities import Client
from loggers.logging_utils import get_logger
from typing import Union

if TYPE_CHECKING:
    from api.src.models.screener import Screener
    from api.src.models.validator import Validator

logger = get_logger(__name__)

async def handle_inform_evaluation_completed(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle inform_evaluation_completed message from a validator/screener"""

    evaluation_id = response_json.get("evaluation_id")
    version_id = response_json.get("version_id")
    completed_at = response_json.get("completed_at")
    
    logger.info(f"XXXXXXXXXX Received inform_evaluation_completed: evaluation_id {evaluation_id} from {client.get_type()} {client.hotkey}")

    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring inform evaluation completed request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    client: Union["Validator", "Screener"] = client
    
    if not evaluation_id:
        return {"status": "error", "message": "Missing evaluation_id"}
    
    try:
        logger.info(f"Evaluation {evaluation_id} completed by {client.get_type()} {client.hotkey}. Finishing evaluation.")
        
        # Force finish the evaluation (skip all_runs_finished check)
        if client.get_type() == "validator":
            logger.info(f"Calling finish_evaluation for {evaluation_id}")
            await client.finish_evaluation(evaluation_id)
            logger.info(f"Called finish_evaluation for {evaluation_id}")
        elif client.get_type() == "screener":
            logger.info(f"Calling finish_screening for {evaluation_id}")
            await client.finish_screening(evaluation_id)
            logger.info(f"Called finish_screening for {evaluation_id}")
        else:
            logger.warning(f"Unknown client type when trying to finish evaluation {evaluation_id}")
            return {"status": "error", "message": "Unknown client type"}

        # Broadcast completion to connected clients
        from api.src.socket.websocket_manager import WebSocketManager
        
        try:
            await WebSocketManager.get_instance().send_to_all_non_validators(
                "evaluation_completed",
                {
                    "evaluation_id": str(evaluation_id),
                    "version_id": str(version_id) if version_id else None,
                    "completed_at": completed_at or datetime.now().isoformat(),
                    "completed_by": client.hotkey
                }
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast evaluation completion: {e}")
        
        return {"status": "success", "message": f"Evaluation {evaluation_id} completed successfully"}
        
    except Exception as e:
        logger.error(f"Error handling evaluation completion for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to handle evaluation completion: {str(e)}"}
