from typing import Dict, Any, Optional
from fastapi import WebSocket

from api.src.utils.logging_utils import get_logger
from api.src.socket.handlers.handle_validator_version import handle_validator_version
from api.src.socket.handlers.handle_get_next_evaluation import handle_get_next_evaluation
from api.src.socket.handlers.handle_start_evaluation import handle_start_evaluation
from api.src.socket.handlers.handle_finish_evaluation import handle_finish_evaluation
from api.src.socket.handlers.handle_upsert_evaluation_run import handle_upsert_evaluation_run
from api.src.socket.handlers.handle_ping import handle_ping

logger = get_logger(__name__)

async def route_message(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any],
    clients: Optional[Dict[WebSocket, Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """Route incoming WebSocket messages to appropriate handlers"""
    
    event = response_json.get("event")
    
    if event == "validator-version":
        # Pass clients and websocket so handler can update state
        return await handle_validator_version(websocket, clients, response_json)
    
    elif event == "get-next-evaluation":
        return await handle_get_next_evaluation(websocket, validator_hotkey, response_json)
    
    elif event == "start-evaluation":
        return await handle_start_evaluation(websocket, validator_hotkey, response_json)
    
    elif event == "finish-evaluation":
        return await handle_finish_evaluation(websocket, validator_hotkey, response_json)
    
    elif event == "upsert-evaluation-run":
        return await handle_upsert_evaluation_run(websocket, validator_hotkey, response_json)
    
    elif event == "ping":
        return await handle_ping(websocket, validator_hotkey, response_json)
    
    else:
        logger.warning(f"Unknown event type: {event}")
        return {"error": f"Unknown event type: {event}"} 