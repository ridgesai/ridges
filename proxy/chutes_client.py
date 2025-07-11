import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from collections import defaultdict

import httpx

from proxy.config import (
    CHUTES_API_KEY,
    CHUTES_EMBEDDING_URL,
    CHUTES_INFERENCE_URL,
    EMBEDDING_PRICE_PER_SECOND,
    MODEL_PRICING,
    MAX_COST_PER_RUN,
    DEFAULT_MODEL,
)
from proxy.models import GPTMessage

logger = logging.getLogger(__name__)

class ChutesClient:
    """Client for interacting with Chutes API services"""
    
    def __init__(self):
        self.api_key = CHUTES_API_KEY
        self.costs_data_embedding: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"spend": 0, "started_at": None})
        self.costs_data_inference: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"spend": 0, "started_at": None})
        self.cleanup_task: Optional[asyncio.Task] = None
        
        if not self.api_key:
            logger.warning("CHUTES_API_KEY not found in environment variables")
    
    def _ensure_cleanup_task(self):
        """Ensure cleanup task is running"""
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self._cleanup_old_costs())
    
    async def _cleanup_old_costs(self):
        """Clean up old cost data periodically"""
        while True:
            try:
                await asyncio.sleep(3600)  # Clean up every hour
                current_time = datetime.now(timezone.utc)
                
                # Clean up embedding costs older than 24 hours
                for run_id in list(self.costs_data_embedding.keys()):
                    started_at = self.costs_data_embedding[run_id].get("started_at")
                    if started_at and (current_time - started_at).total_seconds() > 86400:
                        del self.costs_data_embedding[run_id]
                        logger.debug(f"Cleaned up old embedding costs for run {run_id}")
                
                # Clean up inference costs older than 24 hours
                for run_id in list(self.costs_data_inference.keys()):
                    started_at = self.costs_data_inference[run_id].get("started_at")
                    if started_at and (current_time - started_at).total_seconds() > 86400:
                        del self.costs_data_inference[run_id]
                        logger.debug(f"Cleaned up old inference costs for run {run_id}")
                        
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
    
    async def embed(self, run_id: str, input_text: str) -> Dict[str, Any]:
        """Get embedding for text input"""
        self._ensure_cleanup_task()
        
        # Check cost limits
        if self.costs_data_embedding[run_id]["spend"] >= MAX_COST_PER_RUN:
            logger.warning(f"Embedding request for run {run_id} exceeded cost limit")
            return {
                "error": f"Agent version has reached the maximum cost ({MAX_COST_PER_RUN}) for this evaluation run. Please do not request more embeddings."
            }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        body = {
            "inputs": input_text
        }
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    CHUTES_EMBEDDING_URL,
                    headers=headers,
                    json=body
                )
                response.raise_for_status()
                
                total_time_seconds = time.time() - start_time
                cost = total_time_seconds * EMBEDDING_PRICE_PER_SECOND
                
                # Update cost tracking
                self.costs_data_embedding[run_id]["spend"] += cost
                if self.costs_data_embedding[run_id]["started_at"] is None:
                    self.costs_data_embedding[run_id]["started_at"] = datetime.now(timezone.utc)
                
                logger.debug(f"Embedding request for run {run_id} completed in {total_time_seconds:.2f}s, cost: ${cost:.6f}")
                
                return response.json()
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error in embedding request for run {run_id}: {e.response.status_code} - {e.response.text}")
            return {
                "error": f"HTTP error in embedding request: {e.response.status_code} - {e.response.text}"
            }
        except httpx.TimeoutException:
            logger.error(f"Timeout in embedding request for run {run_id}")
            return {
                "error": "Embedding request timed out. Please try again."
            }
        except Exception as e:
            logger.error(f"Error in embedding request for run {run_id}: {e}")
            return {
                "error": f"Error in embedding request: {str(e)}"
            }
    
    async def inference(self, run_id: str, messages: List[GPTMessage], 
                       temperature: Optional[float] = None, 
                       model: Optional[str] = None) -> Dict[str, Any]:
        """Get inference response for messages"""
        self._ensure_cleanup_task()
        
        # Set default model if not provided
        if not model:
            model = DEFAULT_MODEL
        
        # Validate model
        if model not in MODEL_PRICING:
            logger.warning(f"Unsupported model requested for run {run_id}: {model}")
            return {
                "error": f"Model {model} not supported. Please use one of the following models: {list(MODEL_PRICING.keys())}"
            }
        
        # Check cost limits
        if self.costs_data_inference[run_id]["spend"] >= MAX_COST_PER_RUN:
            logger.warning(f"Inference request for run {run_id} exceeded cost limit")
            return {
                "error": f"Agent version has reached the maximum cost ({MAX_COST_PER_RUN}) for this evaluation run. Please do not request more inference."
            }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        body = {
            "model": model,
            "messages": [],
            "stream": True,
            "max_tokens": 1024,
            "temperature": temperature if temperature is not None else 0.7
        }
        
        # Convert messages to dict format
        if messages:
            for message in messages:
                if message:
                    body['messages'].append({
                        "role": message.role,
                        "content": message.content
                    })
        
        logger.debug(f"Inference request for run {run_id} with model {model}")
        
        response_text = ""
        total_tokens = 0
        
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    CHUTES_INFERENCE_URL,
                    headers=headers,
                    json=body
                ) as response:
                    
                    if response.status_code != 200:
                        error_text = await response.aread()
                        if isinstance(error_text, bytes):
                            error_message = error_text.decode()
                        else:
                            error_message = str(error_text)
                        logger.error(f"Inference API request failed for run {run_id}: {response.status_code} - {error_message}")
                        return {
                            "error": f"API request failed with status {response.status_code}: {error_message}"
                        }
                    
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
            
            # Update cost tracking
            self.costs_data_inference[run_id]["spend"] += cost
            if self.costs_data_inference[run_id]["started_at"] is None:
                self.costs_data_inference[run_id]["started_at"] = datetime.now(timezone.utc)
            
            logger.debug(f"Inference request for run {run_id} completed, tokens: {total_tokens}, cost: ${cost:.6f}")
            
            return {
                "content": response_text,
                "tokens": total_tokens,
                "cost": cost,
                "model": model
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error in inference request for run {run_id}: {e.response.status_code} - {e.response.text}")
            return {
                "error": f"HTTP error in inference request: {e.response.status_code} - {e.response.text}"
            }
        except httpx.TimeoutException:
            logger.error(f"Timeout in inference request for run {run_id}")
            return {
                "error": "Inference request timed out. Please try again."
            }
        except Exception as e:
            logger.error(f"Error in inference request for run {run_id}: {e}")
            return {
                "error": f"Error in inference request: {str(e)}"
            }
    
 