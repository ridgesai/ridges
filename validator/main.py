import asyncio

from dotenv import load_dotenv

from validator.utils.logging import get_logger
load_dotenv()

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from validator.tasks.log_drain import log_drain_task

logger = get_logger(__name__)

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    It also starts the log drain task to periodically send logs to the platform.
    """
    # Start both the websocket app and log drain task concurrently
    await asyncio.gather(
        WebsocketApp().start(),
        log_drain_task(),
        return_exceptions=True
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
