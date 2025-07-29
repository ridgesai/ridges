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

    async def inference(
        self,
        run_id: UUID = None,
        messages: List[GPTMessage] = None,
        temperature: float = None,
        model: str = None,
    ) -> Dict[str, Any]:
        """Get inference response for messages"""

        # Validate model
        if model not in MODEL_PRICING:
            logger.warning(f"Unsupported model requested for run {run_id}: {model}")
            return {
                "error": f"Model {model} not supported. Please use one of the following models: {list(MODEL_PRICING.keys())}"
            }

        # Convert messages to dict format for database storage
        messages_dict = []
        if messages:
            for message in messages:
                if message:
                    messages_dict.append({"role": message.role, "content": message.content})

        # Create inference record in database (skip in dev mode)
        inference_id = None
        if ENV != 'dev':
            inference_id = await create_inference(run_id, messages_dict, temperature, model, "Chutes")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": model,
            "messages": messages_dict,
            "stream": True,
            "max_tokens": 1024,
            "temperature": temperature,
            "seed": random.randint(0, 2**32 - 1),
        }

        logger.debug(f"Inference request for run {run_id} with model {model}")

        response_text = ""
        total_tokens = 0

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", CHUTES_INFERENCE_URL, headers=headers, json=body) as response:

                    if response.status_code != 200:
                        error_text = await response.aread()
                        if isinstance(error_text, bytes):
                            error_message = error_text.decode()
                        else:
                            error_message = str(error_text)
                        logger.error(
                            f"Inference API request failed for run {run_id}: {response.status_code} - {error_message}"
                        )
                        # Update inference record with error (skip in dev mode)
                        if ENV != 'dev' and inference_id:
                            await update_inference(inference_id, 0.0, f"API request failed: {error_message}", 0, None, response.status_code)
                        return {"error": f"API request failed with status {response.status_code}: {error_message}"}

                    # Process streaming response
                    async for chunk in response.aiter_lines():
                        if chunk:
                            chunk_str = chunk.strip()
                            if chunk_str.startswith("data: "):
                                chunk_data = chunk_str[6:]  # Remove "data: " prefix

                                if chunk_data == "[DONE]":
                                    break

                                try:
                                    chunk_json = json.loads(chunk_data)
                                    if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                        choice = chunk_json["choices"][0]
                                        if "delta" in choice and "content" in choice["delta"]:
                                            content = choice["delta"]["content"]
                                            if content:
                                                response_text += content

                                        # Track token usage if available
                                        usage = chunk_json.get("usage")
                                        if usage:
                                            total_tokens = usage.get("total_tokens", 0)

                                except json.JSONDecodeError:
                                    # Skip malformed JSON chunks
                                    continue

            # Calculate cost based on tokens
            cost = (total_tokens / 1_000_000) * MODEL_PRICING[model]

            # Update inference record with cost and response (skip in dev mode)
            if ENV != 'dev' and inference_id:
                await update_inference(inference_id, cost, response_text, total_tokens, None, 200)

            logger.debug(f"Inference request for run {run_id} completed, tokens: {total_tokens}, cost: ${cost:.6f}")

            # Validate that we received actual content
            if not response_text.strip():
                error_msg = f"Chutes API returned empty response for model {model}. This may indicate API issues or malformed streaming response."
                logger.error(f"Empty response for run {run_id}: {error_msg}")
                
                # Update inference record with error (skip in dev mode)
                if ENV != 'dev' and inference_id:
                    await update_inference(inference_id, 0.0, error_msg, 0, None, 200)
                
                return {"error": error_msg}

            return response_text

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error in inference request for run {run_id}: {e.response.status_code} - {e.response.text}"
            )
            # Update inference record with error (skip in dev mode)
            if ENV != 'dev' and inference_id:
                await update_inference(
                    inference_id,
                    0.0,
                    f"HTTP error: {e.response.status_code} - {e.response.text}",
                    0,
                    None,
                    e.response.status_code,
                )
            return {"error": f"HTTP error in inference request: {e.response.status_code} - {e.response.text}"}
        except httpx.TimeoutException:
            logger.error(f"Timeout in inference request for run {run_id}")
            # Update inference record with error (skip in dev mode)
            if ENV != 'dev' and inference_id:
                await update_inference(inference_id, 0.0, "Inference request timed out", 0, None, 408)
            return {"error": "Inference request timed out. Please try again."}
        except Exception as e:
            logger.error(f"Error in inference request for run {run_id}: {e}")
            # Update inference record with error (skip in dev mode)
            if ENV != 'dev' and inference_id:
                await update_inference(inference_id, 0.0, str(e), 0, None, 500)
            return {"error": f"Error in inference request: {str(e)}"}