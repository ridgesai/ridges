from typing import Dict, Any, Optional
from fastapi import WebSocket

from api.src.utils.process_tracking import process_context
from api.src.utils.logging_utils import get_logger
from api.src.socket.handlers.handle_validator_info import handle_validator_info
from api.src.socket.handlers.handle_get_next_evaluation import handle_get_next_evaluation
from api.src.socket.handlers.handle_start_evaluation import handle_start_evaluation
from api.src.socket.handlers.handle_finish_evaluation import handle_finish_evaluation
from api.src.socket.handlers.handle_upsert_evaluation_run import handle_upsert_evaluation_run

logger = get_logger(__name__)

async def route_message(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any],
    clients: Optional[Dict[WebSocket, Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """Route incoming WebSocket messages to appropriate handlers"""
    
    event = response_json.get("event")
    
    if event == "validator-info":
        # Pass clients and websocket so handler can update state
        with process_context("handle-validator-info") as process_id:
            logger.debug(f"Platform received validator-info from a client with validator hotkey {validator_hotkey}. Beginning process handle-validator-info with process ID: {process_id}.")
            result = await handle_validator_info(websocket, clients, response_json)
            logger.debug(f"Completed handle-validator-info with process ID {process_id}.")
            return result
    
    elif event == "get-next-evaluation":
        with process_context("handle-get-next-evaluation") as process_id:
            logger.debug(f"Platform received get-next-evaluation from a client with validator hotkey {validator_hotkey}. Beginning process handle-get-next-evaluation with process ID: {process_id}.")
            result = await handle_get_next_evaluation(websocket, validator_hotkey, response_json)
            logger.debug(f"Completed handle-get-next-evaluation with process ID {process_id}.")
            return result
    
    elif event == "start-evaluation":
        return await handle_start_evaluation(websocket, validator_hotkey, response_json)
    
    elif event == "finish-evaluation":
        return await handle_finish_evaluation(websocket, validator_hotkey, response_json)
    
    elif event == "upsert-evaluation-run":
        return await handle_upsert_evaluation_run(websocket, validator_hotkey, response_json)
    
    else:
        logger.warning(f"Unknown event type: {event}")
        return {"error": f"Unknown event type: {event}"} 