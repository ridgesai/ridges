import logging
import os
from contextlib import asynccontextmanager
from typing import Optional
from functools import wraps

import asyncpg

from proxy.models import EvaluationRun, SandboxStatus

logger = logging.getLogger(__name__)

class DBManager:
    """Database connection manager for the proxy server"""
    
    def __init__(self, *, user: str, password: str, host: str, port: int, database: str,
                 min_con: int = 2, max_con: int = 10):
        self.conn_args = dict(user=user, password=password, host=host, port=port, database=database)
        self.min_con = min_con
        self.max_con = max_con
        self.pool: Optional[asyncpg.Pool] = None

    async def open(self) -> None:
        """Initialize the connection pool"""
        self.pool = await asyncpg.create_pool(
            **self.conn_args,
            min_size=self.min_con,
            max_size=self.max_con,
            max_inactive_connection_lifetime=300,
            statement_cache_size=1_000,
        )
        logger.info(f"Database connection pool initialized with {self.min_con}-{self.max_con} connections")

    async def close(self) -> None:
        """Gracefully close the connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool"""
        if not self.pool:
            raise RuntimeError("Connection pool is not initialized yet.")
        async with self.pool.acquire() as con:
            yield con

# Initialize database manager with environment variables
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

db_manager = DBManager(
    user=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=int(DB_PORT),
    database=DB_NAME,
)

def db_operation(func):
    """Decorator to handle database operations with logging and transaction management"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with db_manager.acquire() as conn:
            try:
                return await func(conn, *args, **kwargs)
            except Exception as e:
                logger.error(f"Database operation failed in {func.__name__}: {e}")
                raise
    return wrapper

@db_operation
async def get_evaluation_run_by_id(conn: asyncpg.Connection, run_id: str) -> Optional[EvaluationRun]:
    """Get evaluation run by run_id"""
    try:
        row = await conn.fetchrow("""
            SELECT run_id, status
            FROM evaluation_runs 
            WHERE run_id = $1
        """, run_id)
        
        if not row:
            return None
        
        return EvaluationRun(
            run_id=row['run_id'],
            status=SandboxStatus(row['status'])
        )
        
    except Exception as e:
        logger.error(f"Error fetching evaluation run {run_id}: {e}")
        raise

 