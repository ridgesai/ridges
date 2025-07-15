import asyncio
import json
from contextlib import asynccontextmanager
import os
from typing import Any, List, Tuple
from functools import wraps
from loggers.logging_utils import get_logger

import asyncpg

logger = get_logger(__name__)

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
        logger.info(f"Initializing database connection to {self.conn_args['database']} on {self.conn_args['host']}:{self.conn_args['port']}.")
        logger.info("Creating connection pool...")
        self.pool = await asyncpg.create_pool(
            **self.conn_args,
            min_size=self.min_con,
            max_size=self.max_con,
            max_inactive_connection_lifetime=300,
            statement_cache_size=1_000,
            command_timeout=30,
            server_settings={
                'idle_in_transaction_session_timeout': '300000',  # 5 minutes
                'statement_timeout': '30000',  # 30 seconds
                'lock_timeout': '30000',  # 30 seconds
            }
        )
        logger.info("Ensuring schema...")
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

async def _flush_to_db(db: DBManager, records: List[Tuple[str, dict[str, Any]]]):
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
async def batch_writer(stop_event: asyncio.Event, queue: asyncio.Queue, FLUSH_MS: int = 500, BATCH_SIZE: int = 1000):
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

new_db = DBManager(
    user=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=int(DB_PORT),
    database=DB_NAME,
)

def db_operation(func):
    """Decorator to handle database operations with logging and transaction rollback"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = asyncio.get_event_loop().time()
        async with new_db.acquire() as conn:
            async with conn.transaction():
                try:
                    result = await func(conn, *args, **kwargs)
                    duration = asyncio.get_event_loop().time() - start_time
                    if duration > 5:  # Log slow queries
                        logger.warning(f"Slow query in {func.__name__}: {duration:.2f}s")
                    return result
                except Exception as e:
                    duration = asyncio.get_event_loop().time() - start_time
                    logger.error(f"Database operation failed in {func.__name__} after {duration:.2f}s: {e}")
                    # Context manager will roll back transaction, reversing any failed commits
                    raise

    return wrapper

async def get_pool_status() -> dict:
    """
    Get connection pool status information.
    """
    try:
        if not new_db.pool:
            return {"error": "Connection pool not initialized"}
        
        return {
            "pool_size": new_db.pool.get_size(),
            "checked_out": new_db.pool.get_size() - new_db.pool.get_idle_size(),
            "idle": new_db.pool.get_idle_size(),
            "checked_in": new_db.pool.get_idle_size()
        }
    except Exception as e:
        logger.error(f"Error getting pool status: {str(e)}")
        return {"error": str(e)}

async def check_idle_transactions() -> List[dict]:
    """
    Check for idle transactions that may be blocking operations.
    """
    try:
        async with new_db.acquire() as conn:
            query = """
            SELECT pid, state, query_start, state_change, query, application_name
            FROM pg_stat_activity 
            WHERE state = 'idle in transaction' 
            AND query_start < NOW() - INTERVAL '5 minutes'
            ORDER BY query_start;
            """
            result = await conn.fetch(query)
            return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error checking idle transactions: {str(e)}")
        return []

async def terminate_idle_transactions() -> int:
    """
    Terminate idle transactions older than 10 minutes.
    Returns number of terminated connections.
    """
    try:
        async with new_db.acquire() as conn:
            query = """
            SELECT pg_terminate_backend(pid), pid, query_start
            FROM pg_stat_activity 
            WHERE state = 'idle in transaction' 
            AND query_start < NOW() - INTERVAL '10 minutes'
            AND pid != pg_backend_pid();
            """
            result = await conn.fetch(query)
            terminated_count = len(result)
            if terminated_count > 0:
                logger.warning(f"Terminated {terminated_count} idle transactions")
                for row in result:
                    logger.info(f"Terminated PID {row['pid']} (idle since {row['query_start']})")
            return terminated_count
    except Exception as e:
        logger.error(f"Error terminating idle transactions: {str(e)}")
        return 0

