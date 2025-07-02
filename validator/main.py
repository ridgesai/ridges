import asyncio

from dotenv import load_dotenv

from validator.utils.logging import get_logger
load_dotenv()

# Internal package imports
from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    await WebsocketApp().start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
