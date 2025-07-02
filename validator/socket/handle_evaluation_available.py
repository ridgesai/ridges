"""Handler for new agent version events."""

from validator.utils.logging import get_logger

logger = get_logger(__name__)

async def handle_evaluation_available(websocket_app):
    """Handle evaluation available events.

    If no evaluation is currently running, request the next evaluation from the
    platform. Otherwise, silently ignore.
    """
    logger.info("Evaluation available")

    if websocket_app.evaluation_running.is_set():
        logger.info("Evaluation already running – skipping request for next evaluation")
        return

    try:
        await websocket_app.send({"event": "get-next-evaluation"})
        logger.info(f"Requested next evaluation")
    except Exception as e:
        logger.error(f"Failed to request next evaluation: {e}")
