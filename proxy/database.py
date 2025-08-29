import logging
import os
import json
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from functools import wraps
from datetime import datetime
from uuid import UUID

import asyncpg

from proxy.models import EvaluationRun, SandboxStatus, Embedding, Inference
from proxy.config import ENV

logger = logging.getLogger(__name__)

class DBManager:
    """Database connection manager for the proxy server"""
    
    def __init__(self, *, user: str, password: str, host: str, port: int, database: str,
                 min_con: int = 60, max_con: int = 600):
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
if ENV != 'dev':
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
else:
    # In dev mode, create a dummy db_manager that won't be used
    db_manager = None

def db_operation(func):
    """Decorator to handle database operations with logging and transaction management"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if ENV == 'dev':
            # In dev mode, skip database operations
            # logger.debug(f"Skipping database operation {func.__name__} in dev mode")
            return None
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
        logger.error(f"Error in get_evaluation_run_by_id(run_id={run_id}): {e}")
        raise

@db_operation
async def create_embedding(conn: asyncpg.Connection, run_id: UUID, input_text: str) -> UUID:
    """Create a new embedding record and return its ID"""
    try:
        row = await conn.fetchrow("""
            INSERT INTO embeddings (run_id, input_text, created_at)
            VALUES ($1, $2, NOW())
            RETURNING id
        """, run_id, input_text)
        
        return row['id']
        
    except Exception as e:
        logger.error(f"Error in create_embedding(run_id={run_id}, ...): {e}")
        raise

@db_operation
async def update_embedding(conn: asyncpg.Connection, embedding_id: UUID, cost: float, 
                          response: Dict[str, Any]) -> None:
    """Update an embedding record with cost and response"""
    try:
        # Convert response dict to JSON string for JSONB storage
        response_json = json.dumps(response)
        
        await conn.execute("""
            UPDATE embeddings 
            SET cost = $1, response = $2, finished_at = NOW()
            WHERE id = $3
        """, cost, response_json, embedding_id)
        
    except Exception as e:
        logger.error(f"Error in update_embedding(embedding_id={embedding_id}, ...): {e}")
        raise

@db_operation
async def create_inference(conn: asyncpg.Connection, run_id: UUID, messages: List[Dict[str, str]], 
                          temperature: float, model: str, provider: str, status_code: int = None,
                          cost: float = None, response: str = None, total_tokens: int = None) -> UUID:
    """Create a complete inference record"""
    try:
        # Convert messages list to JSON string for JSONB storage
        messages_json = json.dumps(messages)
        
        row = await conn.fetchrow("""
            INSERT INTO inferences (run_id, messages, temperature, model, provider, status_code, cost, response, total_tokens, created_at, finished_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NULL)
            RETURNING id
        """, run_id, messages_json, temperature, model, provider, status_code, cost, response, total_tokens)
        
        return row['id']
        
    except Exception as e:
        logger.error(f"Error in create_inference(run_id={run_id}, ...): {e}")
        raise

@db_operation
async def update_inference(conn: asyncpg.Connection, inference_id: UUID, cost: float, 
                          response: str, total_tokens: int, provider: str = None, 
                          status_code: int = None) -> None:
    """Update an inference record with cost, response, tokens, and optionally provider and status_code"""
    try:
        if provider is not None and status_code is not None:
            await conn.execute("""
                UPDATE inferences 
                SET cost = $1, response = $2, total_tokens = $3, provider = $4, status_code = $5, finished_at = NOW()
                WHERE id = $6
            """, cost, response, total_tokens, provider, status_code, inference_id)
        elif provider is not None:
            await conn.execute("""
                UPDATE inferences 
                SET cost = $1, response = $2, total_tokens = $3, provider = $4, finished_at = NOW()
                WHERE id = $5
            """, cost, response, total_tokens, provider, inference_id)
        elif status_code is not None:
            await conn.execute("""
                UPDATE inferences 
                SET cost = $1, response = $2, total_tokens = $3, status_code = $4, finished_at = NOW()
                WHERE id = $5
            """, cost, response, total_tokens, status_code, inference_id)
        else:
            await conn.execute("""
                UPDATE inferences 
                SET cost = $1, response = $2, total_tokens = $3, finished_at = NOW()
                WHERE id = $4
            """, cost, response, total_tokens, inference_id)
        
    except Exception as e:
        logger.error(f"Error in update_inference(inference_id={inference_id}, ...): {e}")
        raise

@db_operation
async def get_total_inference_cost(conn: asyncpg.Connection, run_id: UUID) -> float:
    """Get total cost of inferences for a run"""
    try:
        row = await conn.fetchrow("""
            SELECT COALESCE(SUM(cost), 0) as total_cost
            FROM inferences
            WHERE run_id = $1
        """, run_id)
        
        return row['total_cost'] if row['total_cost'] is not None else 0.0
        
    except Exception as e:
        logger.error(f"Error in get_total_inference_cost(run_id={run_id}): {e}")
        raise

@db_operation
async def get_total_embedding_cost(conn: asyncpg.Connection, run_id: UUID) -> float:
    """Get total cost of embeddings for a run"""
    try:
        row = await conn.fetchrow("""
            SELECT COALESCE(SUM(cost), 0) as total_cost
            FROM embeddings
            WHERE run_id = $1
        """, run_id)
        
        return row['total_cost'] if row['total_cost'] is not None else 0.0
        
    except Exception as e:
        logger.error(f"Error in get_total_embedding_cost(run_id={run_id}): {e}")
        raise