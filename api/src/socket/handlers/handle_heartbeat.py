from typing import Dict, Any
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from api.src.models.screener import Screener
from api.src.models.validator import Validator
from loggers.logging_utils import get_logger
from api.src.backend.entities import Client

logger = get_logger(__name__)

async def handle_heartbeat(
    websocket: WebSocket,
    clients: Dict[WebSocket, Client],
    response_json: Dict[str, Any]
):
    """
    Handle heartbeat message from a validator or screener.
    Severs the connection if the client is incorrect
    If client is available, call connect to check and assign evaluations
    """
    client: Validator | Screener = clients[websocket]

    alleged_status = response_json["status"] # What the client thinks it's doing
    if alleged_status != client.status:
        logger.warning(f"Client {client.hotkey} status mismatch: Client says {alleged_status}, but Platform says {client.status}")
        await websocket.send_json({"event": "error", "error": f"Client status mismatch: Client says {alleged_status}, but Platform says {client.status}"})
        # raise WebSocketDisconnect()

    if client.status == "available":
        await client.connect()

    if alleged_status == "available" and client.status == "reserving":
        await client.connect()
            
    if alleged_status == "screening":
        client.status = "screening"
