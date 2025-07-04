import os
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, List, Tuple

import asyncpg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException

from api.src.utils.logging_utils import get_logger
from api.src.utils.models import DashboardStats


load_dotenv()  # Load variables from a local .env file, if present
logger = get_logger(__name__)

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

class DBManager:
    """Thin wrapper around an asyncpg connection‑pool."""
    def __init__(self, *, user: str, password: str, host: str, port: int, database: str,
                 min_con: int = 4, max_con: int = 32):
        self.conn_args = dict(user=user, password=password, host=host, port=port, database=database)
        self.min_con = min_con
        self.max_con = max_con
        self.pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        """Initialize the connection‑pool and make sure the schema exists."""
        self.pool = await asyncpg.create_pool(
            **self.conn_args,
            min_size=self.min_con,
            max_size=self.max_con,
            max_inactive_connection_lifetime=300,
            statement_cache_size=1_000,
        )
        await self._ensure_schema()

    async def close(self) -> None:
        """Gracefully close the connection‑pool."""
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def acquire(self):  # type: ignore[func-returns-value]
        if not self.pool:
            raise RuntimeError("Connection pool is not initialized yet.")
        async with self.pool.acquire() as con:
            yield con

    async def executemany(self, sql: str, args_iterable: List[Tuple[Any, ...]]):
        async with self.acquire() as con:
            await con.executemany(sql, args_iterable)

    
    # Make sure schema is valid before starting the service
    async def _ensure_schema(self) -> None:
        """Apply schema from an external .sql file if present; otherwise fall back to
        the minimal `device_events` table so the service can still start."""
        from pathlib import Path
        schema_file = Path(__file__).parent / "postgres_schema.sql"

        async with self.acquire() as con:
            if schema_file.exists():
                sql_text = schema_file.read_text()
                await con.execute(sql_text)
                return
            else:
                raise Exception("Schema file is missing")


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


async def _flush_to_db(records: List[Tuple[str, dict[str, Any]]]):
    """Write a list of (device_id, payload) rows to PostgreSQL in bulk."""
    if not records:
        return

    insert_sql = """
        INSERT INTO device_events (device_id, ts, payload)
        SELECT x.device_id::uuid, now(), x.payload::jsonb
        FROM jsonb_to_recordset($1::jsonb) AS x(device_id uuid, payload jsonb);
    """
    # Convert to JSON that Postgres can UNNEST cheaply
    json_rows = [
        {"device_id": dev_id, "payload": payload} for dev_id, payload in records
    ]
    await db.executemany(insert_sql, [(json.dumps(json_rows),)])


# This lets us queue and drain the queries every 0.5s or 1000 queries, whichever is first
# Allows us to do lots of operations from our clients in batches instead of single paths 
async def batch_writer(stop_event: asyncio.Event):
    """Background coroutine: flushes the queue to DB in batches."""

    buf: List[Tuple[str, dict[str, Any]]] = []
    loop = asyncio.get_running_loop()
    last_flush = loop.time()

    while not stop_event.is_set():
        timeout = max(0.0, FLUSH_MS / 1000 - (loop.time() - last_flush))
        try:
            item = await asyncio.wait_for(queue.get(), timeout)
            buf.append(item)
        except asyncio.TimeoutError:
            pass  # periodic flush

        if len(buf) >= BATCH_SIZE or (
            buf and loop.time() - last_flush >= FLUSH_MS / 1000
        ):
            try:
                await _flush_to_db(buf)
            finally:
                buf.clear()
                last_flush = loop.time()

    # Final drain on shutdown
    if buf:
        await _flush_to_db(buf)


# ---------------------------------------------------------------------------
# 4. FastAPI setup
# ---------------------------------------------------------------------------

app = FastAPI(title="High‑QPS WebSocket Ingest")


@app.on_event("startup")
async def _on_startup() -> None:
    await db.open()
    app.state.stop_event = asyncio.Event()
    global _batch_task
    _batch_task = asyncio.create_task(batch_writer(app.state.stop_event))


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

@app.get("/agents/{version}")
async def get_agent(conn=Depends(get_conn)) -> DashboardStats:
    return

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