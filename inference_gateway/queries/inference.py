import asyncpg

from typing import List
from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime
from utils.database import db_operation
from models.evaluation_run import EvaluationRunStatus
from inference_gateway.models import InferenceMessage



@db_operation
async def create_new_inference(
    conn: asyncpg.Connection,
    *,
    evaluation_run_id: UUID,

    provider: str,
    model: str,
    temperature: float,
    messages: List[InferenceMessage],

    status_code: Optional[int] = None,
    response: Optional[str] = None,

    input_tokens: int = None,
    output_tokens: Optional[int] = None,
    cost: Optional[float] = None,

    request_received_at: Optional[datetime] = None,
    response_sent_at: Optional[datetime] = None
) -> UUID:

    inference_id = uuid4()

    await conn.execute(
        """
        INSERT INTO inferences (
            inference_id,
            evaluation_run_id,

            provider,
            model,
            temperature,
            messages,

            status_code,
            response,

            input_tokens,
            output_tokens,
            cost,
            
            request_received_at,
            response_sent_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        """,
        inference_id,
        evaluation_run_id,

        provider,
        model,
        temperature,
        messages,
        
        status_code,
        response,
        
        input_tokens,
        output_tokens,
        cost,
        
        request_received_at,
        response_sent_at
    )

    return inference_id



@db_operation
async def get_number_of_inferences_for_evaluation_run(conn: asyncpg.Connection, evaluation_run_id: UUID) -> int:
    return await conn.fetchval("""
        SELECT COUNT(*) FROM inferences WHERE evaluation_run_id = $1
    """, evaluation_run_id)