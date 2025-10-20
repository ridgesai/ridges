import time
import asyncio
import asyncpg
import utils.logger as logger

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



async def _begin_db_operation(label: str, query: str):
    id = uuid4()
    running_entry = {
        "id": id,
        "label": label,
        "query": query,
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

    running_info = []
    for entry in DEBUG_QUERIES["running"]:
        seconds_running = now - entry["start_time"]
        running_info.append(f"{entry['label']} - {entry['query']} - {seconds_running:.2f} s")

    slow_info = []
    for entry in DEBUG_QUERIES["slow"]:
        seconds_to_run = entry["end_time"] - entry["start_time"]
        slow_info.append(f"{entry['label']} - {entry['query']} - {seconds_to_run:.2f} s")

    return {
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



def db_operation(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global pool
        async with pool.acquire() as conn:
            debug_conn = DatabaseConnection(conn, f"{func.__name__}()")
            return await func(debug_conn, *args, **kwargs)
    
    return wrapper

def db_transaction(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                debug_conn = DatabaseConnection(conn, f"{func.__name__}()")
                return await func(debug_conn, *args, **kwargs)

    return wrapper