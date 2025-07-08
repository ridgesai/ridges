from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException

from typing import Tuple, Any
import os
import asyncio
from api.src.utils.models import DashboardStats
from api.src.backend.queries_old import get_agent_by_hotkey, create_agent
from api.src.backend.entities import MinerAgent

from api.src.backend.db_manager import DBManager, batch_writer
import json
from dotenv import load_dotenv

from api.src.utils.logging_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

app = FastAPI(title="High‑QPS WebSocket Ingest")

DB_USER = os.getenv("AWS_MASTER_USERNAME")
DB_PASS = os.getenv("AWS_MASTER_PASSWORD")
DB_HOST = os.getenv("AWS_RDS_PLATFORM_ENDPOINT")
DB_NAME = os.getenv("AWS_RDS_PLATFORM_DB_NAME")
DB_PORT = os.getenv("PGPORT", "5432")

if not all([DB_USER, DB_PASS, DB_HOST, DB_NAME]):
    raise RuntimeError(
        "Missing one or more required environment variables: "
        "AWS_MASTER_USERNAME, AWS_MASTER_PASSWORD, "
        "AWS_RDS_PLATFORM_ENDPOINT, AWS_RDS_PLATFORM_DB_NAME"
    )

# Batching Queue & Writer Task
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))
FLUSH_MS = int(os.getenv("FLUSH_MS", "500"))  # Flush every 500 ms if batch not full
QUEUE_MAXSIZE = int(os.getenv("INGEST_QUEUE_MAXSIZE", "50000"))  # Back‑pressure guard

# Global singletons
queue: asyncio.Queue[Tuple[str, dict[str, Any]]]  # (device_id, payload)
queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

db = DBManager(
    user=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=int(DB_PORT),
    database=DB_NAME,
)
_batch_task: asyncio.Task | None = None

@app.on_event("startup")
async def _on_startup() -> None:
    await db.open()
    app.state.stop_event = asyncio.Event()
    global _batch_task
    _batch_task = asyncio.create_task(batch_writer(app.state.stop_event, queue))


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    # Signal writer to stop, then await task.
    app.state.stop_event.set()
    if _batch_task:
        await _batch_task
    await db.close()


# Fast API dependancies
async def get_conn(request: Request):
    async with db.acquire() as conn:
        yield conn

# FASTAPI demo endpoints
@app.get("/healthz")
async def health(conn=Depends(get_conn)):
    await conn.execute("SELECT 1;")
    return {"ok": True}


@app.get("/events/{device_id}")
async def recent_events(device_id: str, limit: int = 100, conn=Depends(get_conn)):
    rows = await conn.fetch(
        """
        SELECT id, ts, payload
        FROM device_events
        WHERE device_id = $1::uuid
        ORDER BY ts DESC
        LIMIT $2
        """,
        device_id,
        limit,
    )
    # Convert asyncpg.Record → dict
    return [dict(r) for r in rows]

@app.get("/test/stats")
async def get_statistics(conn=Depends(get_conn)) -> DashboardStats:
    """
    Retrieves stats on the health of the network, primarily for the dashboard
    """
    try:
        result = await conn.fetch("""
            SELECT
                COUNT(*) as number_of_agents,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as agent_iterations_last_24_hours,
                MAX(score) as top_agent_score,
                MAX(score) - COALESCE(MAX(CASE WHEN created_at <= NOW() - INTERVAL '24 hours' THEN score END), 0) as daily_score_improvement
            FROM agent_versions;
            """)
        
        row = result[0]

        return DashboardStats(
            number_of_agents=row[0],
            agent_iterations_last_24_hours=row[1], 
            top_agent_score=row[2],
            daily_score_improvement=row[3]
        )
    except Exception as e:
        logger.error(f"Error retrieving dashboard statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving dashboard stats. Please try again later."
        )

@app.get("/agenttest")
async def something(miner_hotkey: str):
    """
    Retrieves stats on the health of the network, primarily for the dashboard
    """
    agent = await get_agent_by_hotkey(miner_hotkey)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

from datetime import datetime

@app.post("/create_agent")
async def something():
    """
    Retrieves stats on the health of the network, primarily for the dashboard
    """
    agent = await create_agent(MinerAgent(
        miner_hotkey="testtest",
        name="bruh",
        latest_version=0,
        last_updated=datetime.now(),
        created_at=datetime.now(),
    ))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


# WS
@app.websocket("/ws/{device_id}")
async def device_ws(websocket: WebSocket, device_id: str):
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text("Invalid JSON, ignored")
                continue

            try:
                queue.put_nowait((device_id, payload))
            except asyncio.QueueFull:
                # Apply back‑pressure to client
                await websocket.send_text("Server overloaded, slow down")
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# 8. Dev convenience entry‑point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("RELOAD", "True").lower() in {"1", "true", "yes"}),
    )