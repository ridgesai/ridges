import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.agents import router as agents_router

from api.src.utils.weights import run_weight_monitor
from api.src.socket.websocket_manager import WebSocketManager

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://localhost:3000", 'https://www.ridges.ai'],
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

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await WebSocketManager.get_instance().handle_connection(websocket)

@app.on_event("startup")
async def startup_event():
    """Start the weight monitor as a background task when the app starts."""
    asyncio.create_task(run_weight_monitor())
