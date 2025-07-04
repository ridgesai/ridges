from fastapi import APIRouter, Depends, HTTPException

from api.src.utils.auth import verify_request
from api.src.utils.chutes import ChutesManager
from api.src.utils.logging_utils import get_logger
from api.src.db.operations import DatabaseManager
from api.src.utils.models import EmbeddingRequest, InferenceRequest

db = DatabaseManager()

logger = get_logger(__name__)

chutes = ChutesManager()


async def embedding(request: EmbeddingRequest):
    evaluation_run = await db.get_evaluation_run(request.run_id)
    if not evaluation_run:
        logger.info(
            f"Embedding for {request.run_id} was requested but no such evaluation run was found in our database")
        raise HTTPException(status_code=404, detail="Evaluation run not found")

    if evaluation_run.status != "sandbox_created":
        logger.info(
            f"Embedding for {request.run_id} was requested but the evaluation run is not in the sandbox_created state")
        raise HTTPException(
            status_code=400, detail="Evaluation run is not in the sandbox_created state")

    try:
        embedding = await chutes.embed(request.run_id, request.input)
    except Exception as e:
        logger.error(f"Error getting embedding for {request.run_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get embedding due to internal server error. Please try again later.")
    logger.debug(f"Embedding for {request.run_id} was requested and returned")
    return embedding


async def inference(request: InferenceRequest):
    try:
        response = await chutes.inference(
            request.run_id,
            request.messages,
            request.temperature,
            request.model
        )
        logger.debug(
            f"Inference for {request.run_id} was requested and returned \"{response}\"")
        return response
    except Exception as e:
        logger.error(f"Error getting inference for {request.run_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get inference due to internal server error. Please try again later.")

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
