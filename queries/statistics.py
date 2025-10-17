from utils.database import db_operation
import asyncpg

@db_operation
async def get_inference_statistics():
    pass

@db_operation
async def top_score(conn: asyncpg.Connection):
    return await conn.fetchval("SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)")

@db_operation
async def agents_created_24_hrs(conn: asyncpg.Connection):
    return await conn.fetchval("SELECT COUNT(*) FROM agents WHERE created_at >= NOW() - INTERVAL '24 hours'")

@db_operation
async def score_improvement_24_hrs(conn: asyncpg.Connection):
    return await conn.fetchval("SELECT COALESCE((SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)) - COALESCE((SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets) - 1), 0), 0)")