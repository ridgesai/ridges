import json

from uuid import UUID, uuid4
from typing import Any, List, Optional
from utils.database import db_operation, DatabaseConnection



def _remove_null_bytes(x: Any) -> Any:
    return x.replace('\x00', '')



@db_operation
async def create_new_embedding(
    conn: DatabaseConnection,
    *,
    evaluation_run_id: UUID,

    provider: str,
    model: str,
    input: str
) -> UUID:

    embedding_id = uuid4()

    await conn.execute(
        """
        INSERT INTO embeddings (
            embedding_id,
            evaluation_run_id,

            provider,
            model,
            input,

            request_received_at
        ) VALUES ($1, $2, $3, $4, $5, NOW())
        """,
        embedding_id,
        evaluation_run_id,

        provider,
        model,
        _remove_null_bytes(input),
    )

    return embedding_id



@db_operation
async def update_embedding_by_id(
    conn: DatabaseConnection,
    *,
    embedding_id: UUID,

    status_code: Optional[int] = None,
    response: Optional[List[float]] = None,
    num_input_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None
) -> None:
    await conn.execute(
        """
        UPDATE embeddings
        SET
            status_code = $2,
            response = $3,
            num_input_tokens = $4,
            cost_usd = $5,

            response_sent_at = NOW()
        WHERE embedding_id = $1
        """,
        embedding_id,
        status_code,
        json.dumps(response),
        num_input_tokens,
        cost_usd
    )