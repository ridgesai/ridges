import asyncpg

from api.src.backend.db_manager import db_operation

@db_operation
async def get_healthcheck_results(conn: asyncpg.Connection, limit: int = 30):
    results = await conn.fetch("""
        SELECT * FROM platform_status_checks
        ORDER BY checked_at DESC
        LIMIT $1
    """, limit)
    
    return [dict(result) for result in results]
