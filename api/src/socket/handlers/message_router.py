from typing import Dict, Any, Optional
from fastapi import WebSocket

from loggers.process_tracking import process_context
from loggers.logging_utils import get_logger
from api.src.backend.entities import Client
from api.src.socket.handlers.handle_validator_info import handle_validator_info
from api.src.socket.handlers.handle_get_next_evaluation import handle_get_next_evaluation
from api.src.socket.handlers.handle_update_evaluation_run import handle_update_evaluation_run
from api.src.socket.handlers.handle_evaluation_run_log import handle_evaluation_run_log

logger = get_logger(__name__)

async def route_message(
    websocket: WebSocket,
    hotkey: str,
    response_json: Dict[str, Any],
    clients: Optional[Dict[WebSocket, Client]] = None
) -> Optional[Dict[str, Any]]:
    """Route incoming WebSocket messages to appropriate handlers"""
    
    event = response_json.get("event")
    client = clients.get(websocket) if clients else None
    
    if event == "validator-info":
        # Special case - validator-info needs the clients dict for authentication
        with process_context("handle-validator-info") as process_id:
            logger.debug(f"Platform received validator-info from a client with hotkey {hotkey}. Beginning process handle-validator-info with process ID: {process_id}.")
            result = await handle_validator_info(websocket, clients, response_json)
            logger.debug(f"Completed handle-validator-info with process ID {process_id}.")
            return result
    
    # For all other events, ensure we have an authenticated client
    if not client or not hasattr(client, 'hotkey'):
        logger.warning(f"Received {event} from unauthenticated client")
        return {"error": "Not authenticated"}
    
    if event == "get-next-evaluation":
        with process_context("handle-get-next-evaluation") as process_id:
            logger.debug(f"Platform received get-next-evaluation from client {client.hotkey}. Beginning process handle-get-next-evaluation with process ID: {process_id}.")
            result = await handle_get_next_evaluation(client, response_json)
            logger.debug(f"Completed handle-get-next-evaluation with process ID {process_id}.")
            return result
    
    elif event == "update-evaluation-run":
        return await handle_update_evaluation_run(client, response_json)
    
    elif event == "evaluation-run-log":
        return await handle_evaluation_run_log(client, response_json)
    
    else:
        logger.warning(f"Unknown event type: {event}")
        return {"error": f"Unknown event type: {event}"} 