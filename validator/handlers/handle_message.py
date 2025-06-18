import json
from asyncio import Event

from shared.logging_utils import get_logger
from validator.handlers.new_agent_version import handle_new_agent_version
from validator.handlers.get_validator_version import handle_get_validator_version
from validator.handlers.agent_for_evaluation import handle_agent_for_evaluation

logger = get_logger(__name__)

async def handle_message(websocket, message: str, evaluation_running: Event):
    """Route incoming websocket message to appropriate handler.

    Parameters
    ----------
    websocket: The active websocket connection (used for replies).
    message: Raw JSON string received from the websocket.
    evaluation_running: Event used to track the progress of the evaluation process.
    """
    json_message = json.loads(message)
    event = json_message.get("event", None)

    match event:
        case "new-agent-version":
            await handle_new_agent_version(websocket, evaluation_running)
        case "get-validator-version":
            await handle_get_validator_version(websocket)
        case "agent-for-evaluation":
            await handle_agent_for_evaluation(websocket, json_message, evaluation_running)
        case _:
            logger.info(f"Received unrecognized message: {message}")