import asyncio

# Internal package imports
from shared.logging_utils import get_logger
from validator.db.schema import init_db
from validator.tasks.run_websocket_session import run_websocket_session
from validator.tasks.weights import weights_update_loop

logger = get_logger(__name__)

async def main():
    init_db()

    evaluation_running = asyncio.Event()

    websocket_task = asyncio.create_task(run_websocket_session(evaluation_running))
    # weights_task = asyncio.create_task(weights_update_loop())

    await websocket_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully")
