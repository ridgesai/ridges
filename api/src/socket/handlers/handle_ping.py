import json
from typing import Dict, Any
from fastapi import WebSocket

from ...utils.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_ping(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle ping message from a validator"""
    timestamp = response_json.get("timestamp")
    
    try:
        await websocket.send_text(json.dumps({"event": "pong", "timestamp": timestamp}))
        logger.debug(f"Responded to ping from validator {validator_hotkey}")
        return {"event": "pong", "timestamp": timestamp}
        
    except Exception as e:
        logger.error(f"Error responding to ping from validator {validator_hotkey}: {str(e)}")
        return {"error": f"Failed to respond to ping: {str(e)}"} 