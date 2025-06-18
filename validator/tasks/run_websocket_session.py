"""Task for maintaining websocket connection."""

import asyncio
import websockets
from shared.logging_utils import get_logger
from validator.handlers.handle_message import handle_message

logger = get_logger(__name__)

PLATFORM_WS_URL = "ws://10.0.0.99:8765"

async def run_websocket_session(evaluation_running: asyncio.Event):
    """Maintain websocket connection and delegate incoming messages."""
    while True:
        try:
            async with websockets.connect(PLATFORM_WS_URL) as websocket:
                logger.info("Connected to server (websocket task)")
                while True:
                    try:
                        message = await websocket.recv()
                        logger.debug(f"WS received: {message}")
                        await handle_message(websocket, message, evaluation_running)
                    except websockets.ConnectionClosed:
                        logger.warning("Websocket connection closed. Reconnecting...")
                        break
        except Exception as e:
            logger.error(f"Websocket task error: {e}")
        await asyncio.sleep(5) 