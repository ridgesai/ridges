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
    await DatabaseManager().init()
    await db.clean_handing_evaluations()

    # Start the ChutesManager cleanup task
    chutes_manager = ChutesManager()
    chutes_manager.start_cleanup_task()

    asyncio.create_task(run_weight_monitor())
    asyncio.create_task(run_weight_setting_loop(5 * 60))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)