import asyncio
import subprocess
import websockets


# Internal package imports
from shared.logging_utils import get_logger
from validator.db.schema import init_db
from validator.handlers.handle_message import handle_message

logger = get_logger(__name__)

PLATFORM_WS_URL = "ws://10.0.0.99:8765"

async def run_websocket_session():
    """Run a single websocket session."""
    async with websockets.connect(PLATFORM_WS_URL) as websocket:
        logger.info("Connected to server")
        while True:
            try:
                message = await websocket.recv()
                logger.info(f"Received: {message}")
                await handle_message(websocket, message)
            except websockets.ConnectionClosed:
                logger.info("Connection closed. Reconnecting...")
                await asyncio.sleep(5)
                break


async def main():
    init_db()
    await run_websocket_session()


if __name__ == "__main__":
    asyncio.run(main())
