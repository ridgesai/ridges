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
    
    # Check if validator hotkey is already connected
    for existing_websocket, existing_client in clients.items():
        if hasattr(existing_client, 'hotkey') and existing_client.hotkey == hotkey:
            logger.warning(f"Rejecting connection: validator hotkey {hotkey} is already connected")
            # Send authentication-failed message
            await websocket.send_text('{"event": "authentication-failed", "error": "Validator hotkey already connected"}')
            await websocket.close()
            return
    
    logger.info(f"Client {hotkey} has been authenticated and connected. Version commit hash: {version_commit_hash}")

    # Replace the base client with the appropriate typed client
    from api.src.socket.websocket_manager import WebSocketManager
    from api.src.models.validator import Validator
    from api.src.models.screener import Screener
    ws_manager = WebSocketManager.get_instance()
    
    # Get old client info
    old_client = ws_manager.clients[websocket]
    
    # Create appropriate client type based on hotkey
    if hotkey.startswith("screener-1-") or hotkey.startswith("screener-2-") or hotkey.startswith("i-0"):  # Legacy i-0 support
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

    # Send validator-connected event to all non-validators before calling connect()
    from datetime import datetime, timezone
    await ws_manager.send_to_all_non_validators("validator-connected", {
        "type": client.get_type(),
        "validator_hotkey": client.hotkey if client.get_type() == "validator" else None,
        "screener_hotkey": client.hotkey if client.get_type() == "screener" else None,
        "status": client.status,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "ip_address": client.ip_address
    })

    await client.connect()
