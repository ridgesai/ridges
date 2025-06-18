"""Handler for new agent version events."""

import json
from shared.logging_utils import get_logger

logger = get_logger(__name__)


async def handle_new_agent_version(websocket, agent_version):
    """Handle new agent version events."""
    logger.info(f"Received agent version: {agent_version}")
    # TODO: Add agent version to database
    # TODO: Add agent version to challenge
    # TODO: Add agent version to response
    # TODO: Add agent version to evaluation run 