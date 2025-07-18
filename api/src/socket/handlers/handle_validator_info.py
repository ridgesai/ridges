import time
from typing import Dict, Any
from fastapi import WebSocket

from loggers.logging_utils import get_logger
from api.src.backend.entities import Client
from api.src.socket.server_helpers import get_relative_version_num

logger = get_logger(__name__)

async def handle_validator_info(
    websocket: WebSocket,
    clients: Dict[WebSocket, Client],
    response_json: Dict[str, Any]
):
    """Handle validator-info message from a validator with cryptographic authentication"""

    # Extract basic information (JSON still uses validator_hotkey)
    hotkey = response_json["validator_hotkey"]
    version_commit_hash = response_json["version_commit_hash"]
    
    logger.info(f"Client {hotkey} has been authenticated and connected. Version commit hash: {version_commit_hash}")

    # Replace the base client with the appropriate typed client
    from api.src.socket.websocket_manager import WebSocketManager
    ws_manager = WebSocketManager.get_instance()
    client = ws_manager.replace_client_after_auth(
        websocket, 
        hotkey, 
        version_commit_hash=version_commit_hash
    )
    
    logger.debug(f"Populated the WebSocket's client dictionary with the following information: hotkey: {client.hotkey}, version_commit_hash: {client.version_commit_hash}")

    # Delegate connection handling to state machine
    from api.src.backend.state_machine import EvaluationStateMachine
    state_machine = EvaluationStateMachine.get_instance()
    
    if client.get_type() == "screener":
        await state_machine.handle_screener_connect(client)
    else:
        await state_machine.handle_validator_connect(client)
