import asyncio
import sys
import hashlib
import json
import shutil

from dotenv import load_dotenv
load_dotenv("validator/.env")

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from validator.utils.pre_embed_tasks import generate_embeddings
from loggers.logging_utils import get_logger
from pathlib import Path
from ddtrace import tracer

logger = get_logger(__name__)

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    # await check_and_generate_embeddings()
    websocket_app = WebsocketApp()
    try:
        await websocket_app.start()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        await websocket_app.shutdown()
        
        # Cancel all remaining tasks
        tasks = [task for task in asyncio.all_tasks() if not task.done()]
        if tasks:
            logger.info(f"Cancelling {len(tasks)} remaining tasks...")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
