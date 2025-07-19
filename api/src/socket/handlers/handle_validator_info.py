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
    from api.src.models.validator import Validator
    from api.src.models.screener import Screener
    ws_manager = WebSocketManager.get_instance()
    
    # Get old client info
    old_client = ws_manager.clients[websocket]
    
    # Create appropriate client type based on hotkey
    if hotkey.startswith("i-0"):
        client = Screener(
            hotkey=hotkey,
            websocket=old_client.websocket,
            ip_address=old_client.ip_address,
            version_commit_hash=version_commit_hash
        )
    else:
        client = Validator(
            hotkey=hotkey,
            websocket=old_client.websocket,
            ip_address=old_client.ip_address,
            version_commit_hash=version_commit_hash
        )
    
    ws_manager.clients[websocket] = client
    
    logger.debug(f"Populated the WebSocket's client dictionary with the following information: hotkey: {client.hotkey}, version_commit_hash: {client.version_commit_hash}")

    await client.connect()
