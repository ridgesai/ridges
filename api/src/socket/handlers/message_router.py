from typing import Dict, Any, Optional
from fastapi import WebSocket

from api.src.socket.handlers.handle_evaluation_run_logs import handle_evaluation_run_logs
from api.src.socket.handlers.handle_heartbeat import handle_heartbeat
from loggers.process_tracking import process_context
from loggers.logging_utils import get_logger
from api.src.backend.entities import Client
from api.src.socket.handlers.handle_validator_info import handle_validator_info
from api.src.socket.handlers.handle_get_next_evaluation import handle_get_next_evaluation
from api.src.socket.handlers.handle_update_evaluation_run import handle_update_evaluation_run
from api.src.socket.handlers.handle_evaluation_run_logs import handle_evaluation_run_logs
from api.src.socket.handlers.handle_system_metrics import handle_system_metrics
from api.src.socket.handlers.handle_inform_evaluation_completed import handle_inform_evaluation_completed
from api.src.utils.config import WHITELISTED_VALIDATOR_IPS

logger = get_logger(__name__)

def check_websocket_ip_auth(websocket: WebSocket, event: str) -> bool:
    """Check if WebSocket request from IP is authorized for protected events"""
    
    # Get client IP
    client_ip = websocket.client.host if websocket.client else None
    if not client_ip:
        logger.warning(f"WebSocket {event} received without client IP information")
        return False
    
    # If no whitelist configured, allow all (with startup warning already shown)
    if not WHITELISTED_VALIDATOR_IPS:
        return True
    
    # Check IP whitelist
    if client_ip not in WHITELISTED_VALIDATOR_IPS:
        logger.warning(f"WebSocket {event} from non-whitelisted IP: {client_ip}")
        return False
    
    logger.debug(f"WebSocket {event} from whitelisted IP: {client_ip}")
    return True

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
        # Check IP whitelist for validator authentication
        if not check_websocket_ip_auth(websocket, event):
            return {"error": "Access denied: IP not whitelisted"}
            
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
    
    # Check IP whitelist for all validator/screener operations
    protected_events = {"get-next-evaluation", "update-evaluation-run", "evaluation-run-logs", "heartbeat", "inform_evaluation_completed"}
    if event in protected_events:
        if not check_websocket_ip_auth(websocket, event):
            return {"error": "Access denied: IP not whitelisted"}
    
    if event == "get-next-evaluation":
        with process_context("handle-get-next-evaluation") as process_id:
            logger.debug(f"Platform received get-next-evaluation from client {client.hotkey}. Beginning process handle-get-next-evaluation with process ID: {process_id}.")
            result = await handle_get_next_evaluation(client, response_json)
            logger.debug(f"Completed handle-get-next-evaluation with process ID {process_id}.")
            return result
    
    elif event == "update-evaluation-run":
        return await handle_update_evaluation_run(client, response_json)
    
    elif event == "evaluation-run-logs":
        return await handle_evaluation_run_logs(client, response_json)
    
    elif event == "heartbeat":
        return await handle_heartbeat(websocket, clients, response_json)
    
    elif event == "system-metrics":
        return await handle_system_metrics(client, response_json)
    
    elif event == "inform_evaluation_completed":
        return await handle_inform_evaluation_completed(client, response_json)
    
    else:
        logger.warning(f"Unknown event type: {event}")
        return {"error": f"Unknown event type: {event}"} 