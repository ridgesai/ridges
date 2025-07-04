from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from api.src.utils.auth import verify_request
from api.src.utils.chutes import ChutesManager
from api.src.utils.logging_utils import get_logger
from api.src.db.operations import DatabaseManager
from api.src.utils.models import EmbeddingRequest, InferenceRequest

db = DatabaseManager()
logger = get_logger(__name__)
chutes = ChutesManager()

# Thread pool executor for high-frequency endpoints
agents_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agents")

async def embedding(request: EmbeddingRequest):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(agents_executor, _embedding_sync, request)

def _embedding_sync(request: EmbeddingRequest):
    # Run the embedding logic in a separate thread
    evaluation_run = asyncio.run(db.get_evaluation_run(request.run_id))
    if not evaluation_run:
        logger.info(f"Embedding for {request.run_id} was requested but no such evaluation run was found in our database")
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    
    if evaluation_run.status != "sandbox_created":
        logger.info(f"Embedding for {request.run_id} was requested but the evaluation run is not in the sandbox_created state")
        raise HTTPException(status_code=400, detail="Evaluation run is not in the sandbox_created state")

    try:
        embedding = asyncio.run(chutes.embed(request.run_id, request.input))
    except Exception as e:
        logger.error(f"Error getting embedding for {request.run_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get embedding due to internal server error. Please try again later.")
    logger.debug(f"Embedding for {request.run_id} was requested and returned")
    return embedding

async def inference(request: InferenceRequest):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(agents_executor, _inference_sync, request)

def _inference_sync(request: InferenceRequest):
    # Run the inference logic in a separate thread
    try:
        response = asyncio.run(chutes.inference(
            request.run_id, 
            request.messages,
            request.temperature,
            request.model
        ))
        logger.debug(f"Inference for {request.run_id} was requested and returned \"{response}\"")
        return response
    except Exception as e:
        logger.error(f"Error getting inference for {request.run_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get inference due to internal server error. Please try again later.")

router = APIRouter()

routes = [
    ("/embedding", embedding),
    ("/inference", inference),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["agents"],
        dependencies=[Depends(verify_request)],
        methods=["POST"]
    )
