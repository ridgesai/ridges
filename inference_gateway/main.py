import uvicorn
import utils.logger as logger
import inference_gateway.config as config

from typing import List
from functools import wraps
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from models.evaluation_run import EvaluationRunStatus
from inference_gateway.providers.chutes import ChutesProvider
from utils.database import initialize_database, deinitialize_database
from inference_gateway.models import InferenceRequest, EmbeddingRequest
from inference_gateway.queries.evaluation_run import get_evaluation_run_status_by_id
from inference_gateway.queries.inference import create_new_inference, update_inference_by_id, get_number_of_inferences_for_evaluation_run



chutes_provider = None



@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.USE_DATABASE:
        await initialize_database(
            username=config.DATABASE_USERNAME,
            password=config.DATABASE_PASSWORD,
            host=config.DATABASE_HOST,
            port=config.DATABASE_PORT,
            name=config.DATABASE_NAME
        )



    global chutes_provider
    chutes_provider = await ChutesProvider().init()

    # TODO ADAM: uncomment this
    # await chutes_provider.test_all_inference_models()
    # await chutes_provider.test_all_embedding_models()



    yield
    


    if config.USE_DATABASE:
        await deinitialize_database()



app = FastAPI(
    title="Inference Gateway", 
    description="Inference gateway server",
    lifespan=lifespan
)



def handle_http_exceptions(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except HTTPException as e:
            logger.error(f"HTTP exception: {e.status_code} {e.detail}")
            raise
    return wrapper



# NOTE ADAM: inference@main.py -> Handles HTTP exceptions and database
#            inference@providers/provider.py -> Handles logging
#            inference@providers/*.py -> Handles inference
@app.post("/api/inference")
@handle_http_exceptions
async def inference(request: InferenceRequest) -> str:
    if config.USE_DATABASE:
        # Get the status of the evaluation run
        evaluation_run_status = await get_evaluation_run_status_by_id(request.run_id)
        
        # Make sure the evaluation run actually exists
        if evaluation_run_status is None:
            raise HTTPException(
                status_code=400,
                detail=f"No evaluation run exists with the given evaluation run ID {request.run_id}."
            )
        
        # Make sure the evaluation run is in the running_agent state
        if evaluation_run_status != EvaluationRunStatus.running_agent:
            raise HTTPException(
                status_code=400,
                detail=f"The evaluation run with ID {request.run_id} is not in the running_agent state (current state: {evaluation_run_status.value})."
            )

        # Make sure the evaluation run has not already made too many requests
        num_inferences = await get_number_of_inferences_for_evaluation_run(request.run_id)
        if num_inferences >= config.MAX_INFERENCE_REQUESTS_PER_EVALUATION_RUN:
            raise HTTPException(
                status_code=429,
                detail=f"The evaluation run with ID {request.run_id} has already made too many requests (maximum is {config.MAX_INFERENCE_REQUESTS_PER_EVALUATION_RUN})."
            )

    # Make sure we support the model for inference
    if not chutes_provider.is_model_supported_for_inference(request.model):
        raise HTTPException(
            status_code=404,
            detail="The model specified is not supported by Ridges for inference."
        )

    if config.USE_DATABASE:
        inference_id = await create_new_inference(
            evaluation_run_id=request.run_id,

            provider="chutes",
            model=request.model,
            temperature=request.temperature,
            messages=request.messages
        )

    response = await chutes_provider.inference(request.model, request.temperature, request.messages)

    if config.USE_DATABASE:
        await update_inference_by_id(
            inference_id=inference_id,

            status_code=response.status_code,
            response=response.response,
            num_input_tokens=response.num_input_tokens,
            num_output_tokens=response.num_output_tokens,
            cost_usd=response.cost_usd
        )
    
    return response.response



@app.post("/api/embedding")
@handle_http_exceptions
async def embedding(request: EmbeddingRequest) -> List[float]:
    # TODO ADAM
    raise HTTPException(status_code=501, detail="Embedding is unimplemented")



if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)