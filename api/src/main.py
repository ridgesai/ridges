import asyncio
from fastapi import FastAPI, WebSocket
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

app = FastAPI()
server = WebSocketManager()
db = DatabaseManager()

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

@app.on_event("startup")
async def startup_event():
    await DatabaseManager().init()
    await db.clean_hanging_evaluations()

    # Start the ChutesManager cleanup task
    chutes_manager = ChutesManager()
    chutes_manager.start_cleanup_task()

    # asyncio.create_task(run_weight_monitor())
    asyncio.create_task(run_weight_setting_loop(30))
    asyncio.create_task(run_evaluation_cleanup_loop())
    asyncio.create_task(run_connection_pool_monitor())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)