import asyncio
import sys
import hashlib
import json

from dotenv import load_dotenv

from validator.utils.logging import get_logger
load_dotenv()

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from validator.utils.pre_embed_tasks import generate_embeddings
from validator.config import EASY_INSTANCES, MEDIUM_INSTANCES
from pathlib import Path

REPO_EMBEDS_DIR = Path(__file__).parent / 'repo_embeds'

logger = get_logger(__name__)

async def check_and_generate_embeddings():
    tasks = EASY_INSTANCES + MEDIUM_INSTANCES
    config_hash = hashlib.sha256(str(tasks).encode()).hexdigest()
    manifest_path = REPO_EMBEDS_DIR / 'manifest.json'
    if not manifest_path.exists():
        asyncio.create_task(asyncio.to_thread(generate_embeddings))
        logger.info('Starting background embedding generation')
        return
    with open(manifest_path) as f:
        manifest = json.load(f)
    if manifest.get('config_hash') != config_hash:
        asyncio.create_task(asyncio.to_thread(generate_embeddings))
        logger.info('Config changed - regenerating embeddings in background')

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    await check_and_generate_embeddings()
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
