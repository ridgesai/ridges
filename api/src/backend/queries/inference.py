import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import InferenceSummary

@db_operation
async def get_inferences(conn: asyncpg.Connection, since_hours: int) -> list[InferenceSummary]:
    inferences = await conn.fetch(f"""
        SELECT temperature, model, cost, total_tokens, created_at, finished_at, provider, status_code 
        FROM inferences WHERE created_at >= NOW() - INTERVAL '{since_hours} hours'
    """)
    
    return [InferenceSummary(**inference) for inference in inferences]
