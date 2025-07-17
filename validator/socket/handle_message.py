import json
import time

from validator.config import SCREENER_MODE
from loggers.logging_utils import get_logger
from validator.socket.handle_evaluation import handle_evaluation
from validator.socket.handle_evaluation_available import handle_evaluation_available
from ddtrace import tracer

logger = get_logger(__name__)

@tracer.wrap(resource="handle-message")
async def handle_message(websocket_app, message: str):
    """Route incoming websocket message to appropriate handler.

    Parameters
    ----------
    websocket_app: The active websocket connection (used for replies).
    message: Raw JSON string received from the websocket.
    """
    json_message = json.loads(message)
    event = json_message.get("event", None)

    if SCREENER_MODE:
        if event == "screen-agent":
            await handle_evaluation(websocket_app, json_message)
        else:
            logger.info(f"Screener received unrecognized message: {message}")
        return

    match event:
        case "evaluation-available":
            await handle_evaluation_available(websocket_app)
        case "evaluation":
            await handle_evaluation(websocket_app, json_message)
        case "set-weights":
            from validator.socket.handle_set_weights import handle_set_weights  # Local import to avoid circular deps
            await handle_set_weights(websocket_app, json_message)
        case "authentication-failed":
            error_msg = json_message.get("error", "Authentication failed")
            logger.error(f"Authentication failed: {error_msg}")
            websocket_app.authentication_failed = True
            raise SystemExit(f"FATAL: {error_msg}")
        case _:
            logger.info(f"Validator received unrecognized message: {message}")