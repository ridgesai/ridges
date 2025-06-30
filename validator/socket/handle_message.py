import json

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
        case "evaluation-run-finished":
            pass
        case _:
            logger.info(f"Received unrecognized message: {message}")