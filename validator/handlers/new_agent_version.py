"""Handler for new agent version events."""

import json
from shared.logging_utils import get_logger
from validator.config import validator_hotkey

logger = get_logger(__name__)


async def handle_new_agent_version(websocket, evaluation_running):
    """Handle new agent version events.

    If no evaluation is currently running, request the next version from the
    platform. Otherwise, silently ignore.
    """
    logger.info("New agent version announcement received")

    if evaluation_running.is_set():
        logger.info("Evaluation already running – skipping request for next version")
        return

    request = {
        "event": "request-agent-for-evaluation",
        "validator_hotkey": validator_hotkey.ss58_address,
    }
    try:
        await websocket.send(json.dumps(request))
        logger.info(f"Requested next agent version: {request}")
    except Exception as e:
        logger.error(f"Failed to request next agent version: {e}")
