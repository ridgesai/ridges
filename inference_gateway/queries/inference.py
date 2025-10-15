import json
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
    messages: List[InferenceMessage]
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

            request_received_at
        ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
        """,
        inference_id,
        evaluation_run_id,

        provider,
        model,
        temperature,
        json.dumps(messages)
    )

    return inference_id



@db_operation
async def update_inference_by_id(
    conn: asyncpg.Connection,
    *,
    inference_id: UUID,

    status_code: Optional[int] = None,
    response: Optional[str] = None,
    num_input_tokens: Optional[int] = None,
    num_output_tokens: Optional[int] = None,
    cost: Optional[float] = None
) -> None:
    await conn.execute(
        """
        UPDATE inferences
        SET
            status_code = $2,
            response = $3,
            num_input_tokens = $4,
            num_output_tokens = $5,
            cost = $6
            
            response_sent_at = NOW()
        WHERE inference_id = $1
        """,
        inference_id,
        status_code,
        response,
        num_input_tokens,
        num_output_tokens,
        cost
    )



@db_operation
async def get_number_of_inferences_for_evaluation_run(conn: asyncpg.Connection, evaluation_run_id: UUID) -> int:
    return await conn.fetchval("""
        SELECT COUNT(*) FROM inferences WHERE evaluation_run_id = $1
    """, evaluation_run_id)