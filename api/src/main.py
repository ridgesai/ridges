# TODO: cleanup how we set this all up
from dotenv import load_dotenv
load_dotenv()

from api.src.backend.db_manager import new_db, batch_writer

import os
from typing import Tuple, Any
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.src.utils.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.agents import router as agents_router
from api.src.endpoints.scoring import router as scoring_router, run_weight_setting_loop

from api.src.utils.weights import run_weight_monitor
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.chutes import ChutesManager
from api.src.db.operations import DatabaseManager   

logger = get_logger(__name__)

_batch_task: asyncio.Task | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    await new_db.open()
    logger.info(f"New DB pool from lifespan: {new_db}")
    logger.info("Database connection pool opened")
    await DatabaseManager().init()
    app.state.stop_event = asyncio.Event()
    global _batch_task
    _batch_task = asyncio.create_task(batch_writer(app.state.stop_event, queue))

    await db.clean_hanging_evaluations()
    ChutesManager().start_cleanup_task()
    asyncio.create_task(run_weight_setting_loop(30))
    asyncio.create_task(run_evaluation_cleanup_loop())
    asyncio.create_task(run_connection_pool_monitor())
    yield

    # TODO: Handle endpts for new db manager
    app.state.stop_event.set()
    if _batch_task:
        await _batch_task
    await new_db.close()

app = FastAPI(lifespan=lifespan)
server = WebSocketManager()
db = DatabaseManager()

# Batching Queue & Writer Task
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))
FLUSH_MS = int(os.getenv("FLUSH_MS", "500"))  # Flush every 500 ms if batch not full
QUEUE_MAXSIZE = int(os.getenv("INGEST_QUEUE_MAXSIZE", "50000"))  # Back‑pressure guard

# Global singletons
queue: asyncio.Queue[Tuple[str, dict[str, Any]]]  # (device_id, payload)
queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", 'https://www.ridges.ai'],
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

app.include_router(
    upload_router,
    prefix="/upload",
)

app.include_router(
    retrieval_router,
    prefix="/retrieval",
)

# Disabled agents router - using EC2 proxy instead
# app.include_router(
#     agents_router,
#     prefix="/agents",
# )

app.include_router(
    scoring_router,
    prefix="/scoring",
)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await WebSocketManager.get_instance().handle_connection(websocket)

async def run_evaluation_cleanup_loop():
    """Run the evaluation cleanup loop every 10 minutes."""
    logger.info("Starting evaluation cleanup loop - running every 10 minutes")
    
    while True:
        try:
            await db.clean_timed_out_evaluations()
            logger.info("Evaluation cleanup completed. Running again in 10 minutes.")
            await asyncio.sleep(10 * 60) 
        except Exception as e:
            logger.error(f"Error in evaluation cleanup loop: {e}")
            await asyncio.sleep(10 * 60)

async def run_connection_pool_monitor():
    """Monitor connection pool status every 5 minutes."""
    logger.info("Starting connection pool monitor - running every 5 minutes")
    
    while True:
        try:
            pool_status = await db.get_pool_status()
            if "error" not in pool_status:
                logger.info(f"Connection pool status: {pool_status}")
                # Log warning if pool usage is high
                if pool_status["checked_out"] > (pool_status["pool_size"] * 0.8):
                    logger.warning(f"High connection pool usage: {pool_status['checked_out']}/{pool_status['pool_size']} connections in use")
            await asyncio.sleep(5 * 60)
        except Exception as e:
            logger.error(f"Error in connection pool monitor: {e}")
            await asyncio.sleep(5 * 60) 

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)