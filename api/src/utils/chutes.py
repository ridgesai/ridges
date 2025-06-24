import os
import dotenv
import requests
import aiohttp
import json 
from typing import List
from datetime import datetime, timedelta
import asyncio

from api.src.utils.logging import get_logger
from api.src.utils.config import MODEL_PRICE_PER_1M_TOKENS
from api.src.utils.models import GPTMessage

logger = get_logger(__name__)

dotenv.load_dotenv()

class ChutesManager:
    def __init__(self):
        self.api_key = os.getenv('CHUTES_API_KEY')
        self.pricing = MODEL_PRICE_PER_1M_TOKENS
        self.costs_data = {}
        self.cleanup_task = None
        self.start_cleanup_task()

    def start_cleanup_task(self):
        """Start the periodic cleanup task to remove cost data that is older than 15 minutes. This is run every 5 minutes."""
        async def cleanup_loop():
            while True:
                logger.info("Started cleaning up old entries from Chutes")
                await self.cleanup_old_entries()
                logger.info("Finished cleaning up old entries from Chutes. Running again in 5 minutes.")
                await asyncio.sleep(300)
        
        self.cleanup_task = asyncio.create_task(cleanup_loop())

    async def cleanup_old_entries(self) -> None:
        """Remove cost data that is older than 15 minutes"""
        current_time = datetime.now()
        keys_to_remove = []
        
        for key, value in self.costs_data.items():
            if current_time - value["started_at"] > timedelta(minutes=15):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self.costs_data[key]
        logger.info(f"Removed {len(keys_to_remove)} old entries from Chutes")

    def embed(self, prompt: str) -> dict:
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json"
        }
        
        body = {
            "inputs": prompt
        }

        response = requests.post(
            "https://chutes-baai-bge-large-en-v1-5.chutes.ai/embed",
            headers=headers,
            json=body
        )

        return response.json()
    
    async def inference(self, run_id: str, messages: List[GPTMessage], temperature: float = 0.7, model: str = "deepseek-ai/DeepSeek-V3-0324"):
        if not model:
            model = "deepseek-ai/DeepSeek-V3-0324"

        if model not in self.pricing:
            logger.info(f"Agent version from run {run_id} requested an unsupported model: {model}.")
            return f"Model {model} not supported. Please use one of the following models: {list(self.pricing.keys())}"
        
        if self.costs_data.get(run_id, {}).get("spend", 0) >= 2:
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
            "temperature": temperature
        }

        for message in messages:
            body['messages'].append({
                "role": message.role,
                "content": message.content
            })

        # TODO: Fix this: don't use streaming
        response_chunks = []
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://llm.chutes.ai/v1/chat/completions", 
                headers=headers,
                json=body
            ) as response:
                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = data.strip()
                            chunk_json = json.loads(chunk)
                            if chunk_json['choices'][0]['delta']['content']:
                                response_chunks.append(chunk_json['choices'][0]['delta']['content'])
                            elif chunk_json['usage']['total_tokens']:
                                total_tokens = chunk_json['usage']['total_tokens']
                                total_cost = total_tokens * self.pricing[model] / 1000000
                                key = run_id
                                self.costs_data[key] = {
                                    "spend": self.costs_data.get(key, {}).get("spend", 0) + total_cost,
                                    "started_at": self.costs_data.get(key, {}).get("started_at", datetime.now())
                                }
                        except Exception as e:
                            logger.warning(f"Error parsing chunk: {e}")
        
        response_text = "".join(response_chunks)
        return response_text
