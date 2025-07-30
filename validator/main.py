import asyncio

from dotenv import load_dotenv
load_dotenv("validator/.env")

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    websocket_app = WebsocketApp()
    try:
        await websocket_app.start()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        await websocket_app.shutdown()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
