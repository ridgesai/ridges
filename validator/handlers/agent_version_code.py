"""Handler for agent version code events."""

from shared.logging_utils import get_logger

logger = get_logger(__name__)


async def handle_agent_version_code(json_message):
    """Handle agent version code events."""
    logger.info(f"Received agent version code: {json_message}") 