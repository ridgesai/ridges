import asyncio
from fastapi import APIRouter, Depends, HTTPException

from api.src.utils.auth import verify_request
from api.src.utils.chutes import ChutesManager
from api.src.utils.logging import get_logger
from api.src.db.operations import DatabaseManager

db = DatabaseManager()

logger = get_logger(__name__)

chutes = ChutesManager()

async def embedding(input: str):
    try:
        embedding = chutes.embed(input)
    except Exception as e:
        logger.error(f"Error getting embedding for {input}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get embedding due to internal server error. Please try again later.")
    logger.info(f"Embedding for {input} was requested and returned")
    return embedding

async def inference(run_id: str, input_text: str = None, input_code: str = None, return_text: bool = False, return_code: bool = False, model: str = None):
    evaluation_run = db.get_evaluation_run(run_id)
    if not evaluation_run:
        logger.info(f"Inference for {run_id} was requested but no such evaluation run was found in our database")
        raise HTTPException(status_code=404, detail="Evaluation run not found")

    if not input_text and not input_code:
        logger.info(f"Inference for {run_id} was requested but no input_text or input_code was provided")
        raise HTTPException(status_code=400, detail="Either input_text or input_code must be provided.")

    if not return_text and not return_code:
        logger.info(f"Inference for {run_id} was requested but no output type was specified.")
        raise HTTPException(status_code=400, detail="Either return_text or return_code must be True.")

    try:
        return await chutes.inference(run_id, input_text, input_code, return_text, return_code, model)
    except Exception as e:
        logger.error(f"Error getting inference for {run_id}: {e}")
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
        methods=["GET"]
    )
