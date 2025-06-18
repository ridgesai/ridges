import json

from shared.logging_utils import get_logger
from validator.handlers import (
    handle_new_agent_version,
    handle_get_validator_version,
    handle_agent_version,
    handle_agent_version_code
)


logger = get_logger(__name__)

async def handle_message(websocket, message):
    json_message = json.loads(message)
    event = json_message.get("event", None)
    
    match event:
        case "new-agent-version":
            await handle_new_agent_version(websocket)
        case "get-validator-version":
            await handle_get_validator_version(websocket)
        case "agent-version":
            await handle_agent_version(json_message)
        case "agent-version-code":
            await handle_agent_version_code(json_message)
        case _:
            logger.info(f"Received unrecognized message: {message}")