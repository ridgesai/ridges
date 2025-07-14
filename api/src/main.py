# TODO: cleanup how we set this all up
from dotenv import load_dotenv
load_dotenv()

import os
from datetime import timedelta
from typing import Optional, Tuple, Any
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.src.backend.db_manager import new_db, batch_writer
from api.src.backend.queries.cleanup import clean_hanging_evaluations, evaluation_cleanup_loop
from loggers.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.scoring import router as scoring_router, run_weight_setting_loop
from api.src.socket.websocket_manager import WebSocketManager
from api.src.endpoints.healthcheck import router as healthcheck_router
from api.src.utils.top_agent_code import update_top_agent_code

logger = get_logger(__name__)

_batch_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    await new_db.open()
    app.state.stop_event = asyncio.Event()
    global _batch_task
    _batch_task = asyncio.create_task(batch_writer(app.state.stop_event, queue))

    await clean_hanging_evaluations()
    await update_top_agent_code()
    asyncio.create_task(run_weight_setting_loop(30))
    asyncio.create_task(evaluation_cleanup_loop(timedelta(minutes=10)))
    # asyncio.create_task(run_weight_monitor(netuid=62, interval_seconds=60))
    yield

    # TODO: Handle endpts for new db manager
    app.state.stop_event.set()
    if _batch_task:
        await _batch_task
    await new_db.close()

app = FastAPI(lifespan=lifespan)
server = WebSocketManager()

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

app.include_router(upload_router, prefix="/upload")
app.include_router(retrieval_router, prefix="/retrieval")
app.include_router(scoring_router, prefix="/scoring")
app.include_router(healthcheck_router)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await WebSocketManager.get_instance().handle_connection(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)
