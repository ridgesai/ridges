# TODO: cleanup how we set this all up
from dotenv import load_dotenv

load_dotenv("api/.env")

import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.src.backend.db_manager import new_db
from loggers.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.scoring import router as scoring_router, run_weight_setting_loop
from api.src.socket.websocket_manager import WebSocketManager
from api.src.endpoints.healthcheck import router as healthcheck_router
from api.src.endpoints.agent_summaries import router as agent_summaries_router

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await new_db.open()
    
    # Simple startup recovery through evaluation model
    from api.src.models.evaluation import Evaluation
    await Evaluation.startup_recovery()
    
    asyncio.create_task(run_weight_setting_loop(30))
    yield

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
app.include_router(agent_summaries_router, prefix="/agent-summaries")
app.include_router(healthcheck_router)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await WebSocketManager.get_instance().handle_connection(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None)
