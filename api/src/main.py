import asyncio
from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi_utils.tasks import repeat_every
import uvicorn

from api.src.db.operations import DatabaseManager
from api.src.utils.auth import verify_request
from api.src.utils.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.agents import router as agents_router
from api.src.endpoints.scoring import router as scoring_router, weight_receiving_agent

from api.src.utils.weights import run_weight_monitor
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.chutes import ChutesManager

logger = get_logger(__name__)

app = FastAPI()
server = WebSocketManager()

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

app.include_router(
    agents_router,
    prefix="/agents",
)

app.include_router(
    scoring_router,
    prefix="/scoring",
)



# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await WebSocketManager.get_instance().handle_connection(websocket)

@app.on_event("startup")
async def startup_event():
    """Start the weight monitor as a background task when the app starts."""
    asyncio.create_task(run_weight_monitor())
    
    # Start the ChutesManager cleanup task
    chutes_manager = ChutesManager()
    chutes_manager.start_cleanup_task()

@app.on_event("startup")
@repeat_every(seconds=72 * 60)
async def tell_validators_to_set_weights():
    """Tell validators to set their weights."""
    await DatabaseManager().init()
    logger.info("Starting weight setting ping")
    weights = await weight_receiving_agent()
    logger.info(f"Received weights from weight receiving agent: {weights}")
    weights_dict = weights.model_dump(mode='json')
    logger.info(f"Sending weights to all validators: {weights_dict}")
    await server.send_to_all_validators("set-weights", weights_dict)
    logger.info("Sent weights to all validators")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)