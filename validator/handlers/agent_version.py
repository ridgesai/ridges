"""Handler for agent version events."""

from shared.logging_utils import get_logger

logger = get_logger(__name__)


async def handle_agent_version(json_message):
    """Handle agent version events."""
    logger.info(f"Received agent version: {json_message}") 