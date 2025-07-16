# TODO: cleanup how we set this all up
from dotenv import load_dotenv

from api.src.utils.subtensor import start_hotkeys_cache, stop_hotkeys_cache
load_dotenv("api/.env")

import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.src.backend.db_manager import new_db
from api.src.backend.queries.cleanup import clean_running_evaluations, evaluation_timeout_cleanup_loop
from loggers.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.scoring import router as scoring_router, run_weight_setting_loop
from api.src.socket.websocket_manager import WebSocketManager
from api.src.endpoints.healthcheck import router as healthcheck_router
from api.src.utils.top_agent_code import update_top_agent_code

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await new_db.open()
    
    # Check database health before proceeding
    from api.src.backend.db_manager import check_db_health
    if not await check_db_health():
        logger.error("Database health check failed, aborting startup")
        raise RuntimeError("Database not responsive")
    
    await clean_running_evaluations()
    start_hotkeys_cache()
    # await update_top_agent_code()
    asyncio.create_task(run_weight_setting_loop(30))
    # asyncio.create_task(evaluation_cleanup_loop(timedelta(minutes=10)))
    # asyncio.create_task(run_weight_monitor(netuid=62, interval_seconds=60))
    yield

    # Cleanup resources
    stop_hotkeys_cache()
    # TODO: Handle endpts for new db manager
    await new_db.close()

app = FastAPI(lifespan=lifespan)
server = WebSocketManager()

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
