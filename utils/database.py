import re
import time
import asyncio
import asyncpg
import contextvars
import utils.logger as logger

from typing import Optional
from functools import wraps
from uuid import UUID, uuid4



async def initialize_database(*, username: str, password: str, host: str, port: int, name: str):
    logger.info(f"Connecting to database {name} as user {username} on {host}:{port}...")

    global pool
    pool = await asyncpg.create_pool(
        user=username,
        password=password,
        host=host,
        port=port,
        database=name
    )

    logger.info(f"Connected to database {name} as user {username} on {host}:{port}.")

async def deinitialize_database():
    logger.info("Disconnecting from database...")
    
    global pool
    await pool.close()

    logger.info("Disconnected from database.")



DEBUG_QUERIES = {
    "running": [],
    "slow": []
}

DEBUG_QUERIES_LOCK = asyncio.Lock()



ACTIVE_CONNECTIONS = 0
ACTIVE_REUSED_CONNECTIONS = 0



async def _begin_db_operation(label: str, query: str):
    id = uuid4()
    running_entry = {
        "id": id,
        "label": label,
        "query": re.sub(r'\s+', ' ', query).strip(),
        "start_time": time.time()
    }
    async with DEBUG_QUERIES_LOCK:
        DEBUG_QUERIES["running"].append(running_entry)
    return id

async def _end_db_operation(id: UUID):
    async with DEBUG_QUERIES_LOCK:
        running_entry = next(x for x in DEBUG_QUERIES["running"] if x["id"] == id)
        DEBUG_QUERIES["running"] = [x for x in DEBUG_QUERIES["running"] if x["id"] != id]
        elapsed = time.time() - running_entry["start_time"]
        if elapsed > 5:
            running_entry["end_time"] = time.time()
            DEBUG_QUERIES["slow"].append(running_entry)



def get_debug_query_info():
    now = time.time()

    running_sorted = sorted(DEBUG_QUERIES["running"],  key=lambda entry: now - entry["start_time"], reverse=True)
    running_info = []
    for entry in running_sorted:
        seconds_running = now - entry["start_time"]
        running_info.append(f"{entry['label']} - {entry['query']} - {seconds_running:.2f} s")

    slow_sorted = sorted(DEBUG_QUERIES["slow"], key=lambda entry: entry["end_time"] - entry["start_time"], reverse=True)
    slow_info = []
    for entry in slow_sorted:
        seconds_to_run = entry["end_time"] - entry["start_time"]
        slow_info.append(f"{entry['label']} - {entry['query']} - {seconds_to_run:.2f} s")

    return {
        "active_connections": ACTIVE_CONNECTIONS,
        "active_reused_connections": ACTIVE_REUSED_CONNECTIONS,
        "running": running_info,
        "slow": slow_info
    }



class DatabaseConnection:
    def __init__(self, conn: asyncpg.Connection, label: str):
        self.conn = conn
        self.label = label

    async def execute(self, query: str, *args, **kwargs):
        id = await _begin_db_operation(self.label, query)
        try:
            return await self.conn.execute(query, *args, **kwargs)
        finally:
            await _end_db_operation(id)

    async def fetch(self, query: str, *args, **kwargs):
        id = await _begin_db_operation(self.label, query)
        try:
            return await self.conn.fetch(query, *args, **kwargs)
        finally:
            await _end_db_operation(id)

    async def fetchrow(self, query: str, *args, **kwargs):
        id = await _begin_db_operation(self.label, query)
        try:
            return await self.conn.fetchrow(query, *args, **kwargs)
        finally:
            await _end_db_operation(id)

    async def fetchval(self, query: str, *args, **kwargs):
        id = await _begin_db_operation(self.label, query)
        try:
            return await self.conn.fetchval(query, *args, **kwargs)
        finally:
            await _end_db_operation(id)



_per_context_conn: contextvars.ContextVar[Optional[DatabaseConnection]] = contextvars.ContextVar('db_connection', default=None)

def db_operation(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global ACTIVE_CONNECTIONS, ACTIVE_REUSED_CONNECTIONS
        
        conn = _per_context_conn.get()
        if conn:
            ACTIVE_REUSED_CONNECTIONS += 1
            result = await func(conn, *args, **kwargs)
            ACTIVE_REUSED_CONNECTIONS -= 1
            return result

        async with pool.acquire() as _conn:
            ACTIVE_CONNECTIONS += 1
            conn = DatabaseConnection(_conn, f"{func.__name__}()")
            token = _per_context_conn.set(conn)
            try:
                return await func(conn, *args, **kwargs)
            finally:
                _per_context_conn.reset(token)
                ACTIVE_CONNECTIONS -= 1
    
    return wrapper