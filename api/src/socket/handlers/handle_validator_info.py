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
    from api.src.backend.entities import Screener, Validator
    ws_manager = WebSocketManager.get_instance()
    
    # Inline the client replacement logic
    old_client = ws_manager.clients[websocket]
    client_data = {
        'ip_address': old_client.ip_address,
        'connected_at': old_client.connected_at,
        'websocket': old_client.websocket,
        'status': 'available',
        'version_commit_hash': version_commit_hash
    }
    
    # Create appropriate client type based on hotkey
    if hotkey.startswith("i-0"):
        client = Screener(hotkey=hotkey, **client_data)
    else:
        client = Validator(hotkey=hotkey, **client_data)
    
    ws_manager.clients[websocket] = client
    
    logger.debug(f"Populated the WebSocket's client dictionary with the following information: hotkey: {client.hotkey}, version_commit_hash: {client.version_commit_hash}")

    from api.src.backend.agent_machine import AgentStateMachine
    state_machine = AgentStateMachine.get_instance()
    
    if client.get_type() == "screener":
        await state_machine.screener_connect(client)
    elif client.get_type() == "validator":
        await state_machine.validator_connect(client)
