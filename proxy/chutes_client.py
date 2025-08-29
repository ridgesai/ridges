import json
import logging
import random
import time
from typing import Dict, List, Any
from uuid import UUID

import httpx

from proxy.config import (
    CHUTES_API_KEY,
    CHUTES_EMBEDDING_URL,
    CHUTES_INFERENCE_URL,
    EMBEDDING_PRICE_PER_SECOND,
    MODEL_PRICING,
    DEFAULT_MODEL,
    ENV,
)
from proxy.models import GPTMessage
from proxy.database import (
    create_embedding,
    update_embedding,
    create_inference,
    update_inference,
)

logger = logging.getLogger(__name__)


class ChutesClient:
    """Client for interacting with Chutes API services"""

    def __init__(self):
        self.api_key = CHUTES_API_KEY

        if not self.api_key:
            logger.warning("CHUTES_API_KEY not found in environment variables")

    async def embed(self, run_id: UUID = None, input_text: str = None) -> Dict[str, Any]:
        """Get embedding for text input"""

        # Create embedding record in database (skip in dev mode)
        embedding_id = None
        if ENV != 'dev':
            embedding_id = await create_embedding(run_id, input_text)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {"inputs": input_text, "seed": random.randint(0, 2**32 - 1)}

        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(CHUTES_EMBEDDING_URL, headers=headers, json=body)
                response.raise_for_status()

                total_time_seconds = time.time() - start_time
                cost = total_time_seconds * EMBEDDING_PRICE_PER_SECOND

                response_data = response.json()

                # Update embedding record with cost and response (skip in dev mode)
                if ENV != 'dev' and embedding_id:
                    await update_embedding(embedding_id, cost, response_data)

                logger.debug(
                    f"Embedding request for run {run_id} completed in {total_time_seconds:.2f}s, cost: ${cost:.6f}"
                )

                return response_data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error in embedding request for run {run_id}: {e.response.status_code} - {e.response.text}"
            )
            # Update embedding record with error (skip in dev mode)
            if ENV != 'dev' and embedding_id:
                await update_embedding(
                    embedding_id,
                    0.0,
                    {"error": f"HTTP error: {e.response.status_code} - {e.response.text}"},
                )
            return {"error": f"HTTP error in embedding request: {e.response.status_code} - {e.response.text}"}
        except httpx.TimeoutException:
            logger.error(f"Timeout in embedding request for run {run_id}")
            # Update embedding record with error (skip in dev mode)
            if ENV != 'dev' and embedding_id:
                await update_embedding(embedding_id, 0.0, {"error": "Embedding request timed out"})
            return {"error": "Embedding request timed out. Please try again."}
        except Exception as e:
            logger.error(f"Error in embedding request for run {run_id}: {e}")
            # Update embedding record with error (skip in dev mode)
            if ENV != 'dev' and embedding_id:
                await update_embedding(embedding_id, 0.0, {"error": str(e)})
            return {"error": f"Error in embedding request: {str(e)}"}