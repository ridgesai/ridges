import uuid
import asyncpg

from api.src.backend.db_manager import db_operation



@db_operation
async def create_evaluation_run_for_evaluation_id(conn: asyncpg.Connection, evaluation_id: str, problem: str) -> uuid.UUID:
    run_id = str(uuid.uuid4())
    await conn.execute("""
        INSERT INTO evaluation_runs (
            run_id,
            evaluation_id,
            swebench_instance_id,
            status,
            started_at
        )
        VALUES (
            $1, -- run_id
            $2, -- evaluation_id
            $3, -- swebench_instance_id
            $4, -- status
            NOW(), -- started_at
        )
    """, run_id, evaluation_id, problem, SandboxStatus.started)
    return run_id