import asyncio

# Internal package imports
from shared.logging_utils import get_logger
from validator.db.schema import init_db
from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

async def main():
    init_db()
    await WebsocketApp().start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
