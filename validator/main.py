import asyncio
import websockets

# Internal package imports
from shared.logging_utils import get_logger
from validator.db.schema import init_db
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
        # Exponential backoff could be added here
        await asyncio.sleep(5)


async def main():
    init_db()

    evaluation_running = asyncio.Event()

    websocket_task = asyncio.create_task(run_websocket_session(evaluation_running))

    await websocket_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully")
