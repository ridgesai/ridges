import asyncpg
import utils.logger as logger

from functools import wraps



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



def db_operation(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global pool
        async with pool.acquire() as conn:
            return await func(conn, *args, **kwargs)
    
    return wrapper

def db_transaction(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global pool
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await func(conn, *args, **kwargs)

    return wrapper