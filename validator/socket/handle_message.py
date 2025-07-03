import json
import time

from validator.utils.logging import get_logger
from validator.socket.handle_evaluation import handle_evaluation
from validator.socket.handle_evaluation_available import handle_evaluation_available

logger = get_logger(__name__)

async def handle_message(websocket_app, message: str):
    """Route incoming websocket message to appropriate handler.

    Parameters
    ----------
    websocket_app: The active websocket connection (used for replies).
    message: Raw JSON string received from the websocket.
    """
    json_message = json.loads(message)
    event = json_message.get("event", None)

    match event:
        case "evaluation-available":
            await handle_evaluation_available(websocket_app)
        case "evaluation":
            await handle_evaluation(websocket_app, json_message)
        case "set-weights":
            from validator.socket.handle_set_weights import handle_set_weights  # Local import to avoid circular deps
            await handle_set_weights(websocket_app, json_message)
        case "pong":
            websocket_app.last_pong_time = time.time()
            logger.debug(f"Received pong response: {json_message.get('timestamp')}")
        case _:
            logger.info(f"Received unrecognized message: {message}")