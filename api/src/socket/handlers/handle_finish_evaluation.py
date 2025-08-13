
from typing import Dict, Any, Optional

from api.src.backend.entities import Client
from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id
from api.src.models.screener import Screener
from api.src.models.validator import Validator
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_finish_evaluation(
    client: Client,
    response_json: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Handle finish-evaluation message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring finish evaluation request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    evaluation_id = response_json["evaluation_id"]
    errored = response_json.get("errored", False)
    reason = response_json.get("reason") if errored else None
    
    # Get the evaluation to check if it's a screener evaluation
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
    if not evaluation:
        logger.warning(f"Evaluation {evaluation_id} not found")
        return {"status": "error", "message": "Evaluation not found"}
    
    is_screener_evaluation = (evaluation.validator_hotkey.startswith("screener-") or 
                             evaluation.validator_hotkey.startswith("i-0"))
    is_screener_client = client.get_type() == "screener"
    
    # Validate client type matches evaluation type
    if is_screener_client and not is_screener_evaluation:
        logger.error(f"Screener {client.hotkey} trying to finish validator evaluation {evaluation_id}")
        return {"status": "error", "message": "Screeners cannot finish validator evaluations"}
    elif not is_screener_client and is_screener_evaluation:
        logger.error(f"Validator {client.hotkey} trying to finish screener evaluation {evaluation_id}")
        return {"status": "error", "message": "Validators cannot finish screener evaluations"}
    
    try:
        logger.info(f"{client.get_type().title()} {client.hotkey} has finished evaluation {evaluation_id}.")
        
        if client.get_type() == "screener":
            screener: Screener = client
            await screener.finish_screening(evaluation_id, errored, reason)
            action = "Screening"
        elif client.get_type() == "validator":
            validator: Validator = client
            await validator.finish_evaluation(evaluation_id, errored, reason)
            action = "Evaluation"
        
        # Broadcast evaluation completion
        try:
            from api.src.socket.websocket_manager import WebSocketManager
            from datetime import datetime
            
            await WebSocketManager.get_instance().send_to_all_non_validators(
                "evaluation_completed",
                {
                    "version_id": str(evaluation.version_id),
                    "miner_hotkey": evaluation.miner_hotkey,
                    "evaluation_id": str(evaluation_id),
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast evaluation completion: {e}")
        
        logger.info(f"{action} {evaluation_id} finished successfully by {client.get_type()} {client.hotkey}")
        return {"status": "success", "message": f"{action} finished successfully"}
            
    except Exception as e:
        logger.error(f"Error finishing evaluation for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to finish evaluation: {str(e)}"} 