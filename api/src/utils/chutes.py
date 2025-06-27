import os
import dotenv
import requests
import aiohttp
import json 
from typing import List
from datetime import datetime, timedelta
import asyncio
import time

from api.src.utils.logging_utils import get_logger
from api.src.utils.config import MODEL_PRICE_PER_1M_TOKENS, EMBEDDING_PRICE_PER_SECOND
from api.src.utils.models import GPTMessage

logger = get_logger(__name__)

dotenv.load_dotenv()

class ChutesManager:
    def __init__(self):
        self.api_key = os.getenv('CHUTES_API_KEY')
        self.pricing = MODEL_PRICE_PER_1M_TOKENS
        self.costs_data_inference = {}
        self.costs_data_embedding = {}
        self.cleanup_task = None
        self.start_cleanup_task()

    def start_cleanup_task(self):
        """Start the periodic cleanup task to remove cost data that is older than 20 minutes. This is run every 5 minutes."""
        async def cleanup_loop():
            while True:
                logger.info("Started cleaning up old entries from Chutes")
                await self.cleanup_old_entries()
                logger.info("Finished cleaning up old entries from Chutes. Running again in 5 minutes.")
                await asyncio.sleep(15)
        
        self.cleanup_task = asyncio.create_task(cleanup_loop())

    async def cleanup_old_entries(self) -> None:
        """Remove cost data that is older than 20 minutes"""
        try:
            current_time = datetime.now()
            keys_to_remove_inference = []
            keys_to_remove_embedding = []

            for key, value in self.costs_data_inference.items():
                if current_time - value["started_at"] > timedelta(minutes=1):
                    keys_to_remove_inference.append(key)
            
            for key, value in self.costs_data_embedding.items():
                if current_time - value["started_at"] > timedelta(minutes=1):
                    keys_to_remove_embedding.append(key)
        
            for key in keys_to_remove_inference:
                del self.costs_data_inference[key]
            for key in keys_to_remove_embedding:
                del self.costs_data_embedding[key]
            logger.info(f"Removed {len(keys_to_remove_inference)} old entries from Chutes inference pricing data")
            logger.info(f"Removed {len(keys_to_remove_embedding)} old entries from Chutes embedding pricing data")
        except Exception as e:
            logger.error(f"Error cleaning up old entries from Chutes pricing data: {e}")

    def embed(self, run_id: str, prompt: str) -> dict:
        if self.costs_data_embedding.get(run_id, {}).get("spend", 0) >= 2:
            logger.info(f"Agent version from run {run_id} has reached the maximum cost from their evaluation run.")
            return f"Your agent version has reached the maximum cost for this evaluation run. Please do not request more embeddings or inference from this agent version."

        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json"
        }
        
        body = {
            "inputs": prompt
        }

        start = time.time()
        response = requests.post(
            "https://chutes-baai-bge-large-en-v1-5.chutes.ai/embed",
            headers=headers,
            json=body
        )
        total_time_seconds = time.time() - start
        
        cost = total_time_seconds * EMBEDDING_PRICE_PER_SECOND

        self.costs_data_embedding[run_id] = {
            "spend": self.costs_data_embedding.get(run_id, {}).get("spend", 0) + cost,
            "started_at": self.costs_data_embedding.get(run_id, {}).get("started_at", datetime.now())
        }

        logger.info(f"Updated embedding spend for run {run_id}: {cost} (total: {self.costs_data_embedding[run_id]['spend']})")

        return response.json()
    
    async def inference(self, run_id: str, messages: List[GPTMessage], temperature: float = 0.7, model: str = "deepseek-ai/DeepSeek-V3-0324"):
        if not model:
            model = "deepseek-ai/DeepSeek-V3-0324"

        if model not in self.pricing:
            logger.info(f"Agent version from run {run_id} requested an unsupported model: {model}.")
            return f"Model {model} not supported. Please use one of the following models: {list(self.pricing.keys())}"
        
        if self.costs_data_inference.get(run_id, {}).get("spend", 0) >= 2:
            logger.info(f"Agent version from run {run_id} has reached the maximum cost from their evaluation run.")
            return f"Your agent version has reached the maximum cost for this evaluation run. Please do not request more inference from this agent version."
        
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json"
        }
        
        body = {
            "model": model,
            "messages": [],
            "stream": True,
            "max_tokens": 1024,
            "temperature": temperature if temperature is not None else 0.7
        }

        # Check if messages is not None before iterating
        if messages is not None:
            for message in messages:
                if message is not None:
                    body['messages'].append({
                        "role": message.role,
                        "content": message.content
                    })

        logger.info(f"Body: {body}")

        response_chunks = []
        total_tokens = 0
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://llm.chutes.ai/v1/chat/completions", 
                    headers=headers,
                    json=body
                ) as response:
                    logger.info(f"Response status: {response.status}")
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"API request failed with status {response.status}: {error_text}")
                        return f"API request failed with status {response.status}: {error_text}"
                    
                    # Read the entire response as text first
                    response_text = await response.text()
                    logger.info(f"Raw response: {response_text}")
                    
                    # Check if response contains an error message despite 200 status
                    if response_text and ("Internal Server Error" in response_text or "exhausted all available targets" in response_text):
                        logger.error(f"API returned error in response body: {response_text}")
                        return f"API Error: {response_text}"
                    
                    # If it's a streaming response, parse the lines
                    if response_text:
                        lines = response_text.strip().split('\n')
                        if lines:
                            for line in lines:
                                if line is not None:
                                    line = line.strip()
                                    if not line:
                                        continue
                                        
                                    if line.startswith("data: "):
                                        data = line[6:]
                                        if data == "[DONE]":
                                            break
                                        
                                        try:
                                            chunk_json = json.loads(data)
                                            
                                            # Extract content from delta
                                            if chunk_json and 'choices' in chunk_json and chunk_json['choices']:
                                                choice = chunk_json['choices'][0]
                                                if choice and 'delta' in choice and 'content' in choice['delta']:
                                                    content = choice['delta']['content']
                                                    if content:
                                                        response_chunks.append(content)
                                            
                                            # Extract usage data
                                            if chunk_json and 'usage' in chunk_json and chunk_json['usage'] is not None and 'total_tokens' in chunk_json['usage']:
                                                total_tokens = chunk_json['usage']['total_tokens']
                                                    
                                        except json.JSONDecodeError:
                                            pass
                        else:
                            logger.error(f"No lines found in response: {response_text}")
                            return f"No lines found in response: {response_text}"
        
        except Exception as e:
            logger.error(f"Error in inference request: {e}", stack_info=True, exc_info=True)
            return f"Error in inference request: {e}"
        
        # Update costs data if we received usage information
        if total_tokens > 0:
            total_cost = total_tokens * self.pricing[model] / 1000000
            key = run_id
            self.costs_data_inference[key] = {
                "spend": self.costs_data_inference.get(key, {}).get("spend", 0) + total_cost,
                "started_at": self.costs_data_inference.get(key, {}).get("started_at", datetime.now())
            }
            logger.info(f"Updated inference spend for run {run_id}: {total_cost} (total: {self.costs_data_inference[key]['spend']})")
        
        response_text = "".join(response_chunks)
        logger.info(f"Final response length: {len(response_text)}")
        
        # If we got no response chunks but the API call succeeded, return a fallback message
        if not response_chunks:
            logger.warning("No response chunks collected, returning fallback message")
            return "No response content received from the model"
        
        return response_text
