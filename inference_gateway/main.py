import random
import uvicorn
import utils.logger as logger
import inference_gateway.config as config

from typing import List
from functools import wraps
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from models.evaluation_run import EvaluationRunStatus
from inference_gateway.cost_hash_map import CostHashMap
from inference_gateway.providers.provider import Provider
from inference_gateway.providers.chutes import ChutesProvider
from inference_gateway.providers.targon import TargonProvider
from queries.evaluation_run import get_evaluation_run_status_by_id
from queries.embedding import create_new_embedding, update_embedding_by_id
from queries.inference import create_new_inference, update_inference_by_id
from inference_gateway.models import EmbeddingRequest, InferenceRequest, InferenceToolMode
from utils.database import initialize_database, get_debug_query_info, deinitialize_database



class WeightedProvider:
    def __init__(self, provider: Provider, weight: int):
        self.provider = provider
        self.weight = weight

providers = []



def get_provider_that_supports_model_for_inference(model_name: str) -> Provider:
    inference_providers = [wp for wp in providers if wp.provider.is_model_supported_for_inference(model_name)]  
    if not inference_providers:
        return None
    chosen = random.choices(inference_providers, weights=[wp.weight for wp in inference_providers], k=1)[0]
    return chosen.provider

def get_provider_that_supports_model_for_embedding(model_name: str) -> Provider:
    embedding_providers = [wp for wp in providers if wp.provider.is_model_supported_for_embedding(model_name)]
    if not embedding_providers:
        return None
    chosen = random.choices(embedding_providers, weights=[wp.weight for wp in embedding_providers], k=1)[0]
    return chosen.provider



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



    global providers
    if config.USE_CHUTES:
        providers.append(WeightedProvider(await ChutesProvider().init(), weight=config.CHUTES_WEIGHT))
    if config.USE_TARGON:
        providers.append(WeightedProvider(await TargonProvider().init(), weight=config.TARGON_WEIGHT))

    for wp in providers:
        if config.TEST_INFERENCE_MODELS:
            await wp.provider.test_all_inference_models()
        if config.TEST_EMBEDDING_MODELS:
            await wp.provider.test_all_embedding_models()



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



cost_hash_map = CostHashMap()



# NOTE ADAM: inference@main.py -> Handles HTTP exceptions and database
#            inference@providers/provider.py -> Handles logging
#            inference@providers/*.py -> Handles inference
@app.post("/api/inference")
@handle_http_exceptions
async def inference(request: InferenceRequest) -> str:
    # If you specify a tool mode of NONE, you must not specify any tools
    if request.tool_mode == InferenceToolMode.NONE and request.tools:
        raise HTTPException(
            status_code=422,
            detail="If you specify a tool mode of NONE, you must not specify any tools."
        )

    # If you specify a tool mode of REQUIRED, you must specify at least one tool
    if request.tool_mode == InferenceToolMode.REQUIRED and not request.tools:
        raise HTTPException(
            status_code=422,
            detail="If you specify a tool mode of REQUIRED, you must specify at least one tool."
        )



    if config.USE_DATABASE and config.CHECK_EVALUATION_RUNS:
        # Get the status of the evaluation run
        evaluation_run_status = await get_evaluation_run_status_by_id(request.evaluation_run_id)
        
        # Make sure the evaluation run actually exists
        if evaluation_run_status is None:
            raise HTTPException(
                status_code=400,
                detail=f"No evaluation run exists with the given evaluation run ID {request.evaluation_run_id}."
            )
        
        # Make sure the evaluation run is in the running_agent state
        if evaluation_run_status != EvaluationRunStatus.running_agent:
            raise HTTPException(
                status_code=400,
                detail=f"The evaluation run with ID {request.evaluation_run_id} is not in the running_agent state (current state: {evaluation_run_status.value})."
            )

        # Make sure the evaluation run has not reached or exceeded its cost limit
        cost = cost_hash_map.get_cost(request.evaluation_run_id)
        if cost >= config.MAX_COST_PER_EVALUATION_RUN_USD:
            raise HTTPException(
                status_code=429,
                detail=f"The evaluation run with ID {request.evaluation_run_id} has reached or exceeded the evaluation run cost limit of {config.MAX_COST_PER_EVALUATION_RUN_USD} USD (current cost: {cost} USD)."
            )

        

    # Make sure we support the model for inference
    provider = get_provider_that_supports_model_for_inference(request.model)
    if not provider:
        raise HTTPException(
            status_code=404,
            detail="The model specified is not supported by Ridges for inference."
        )

    if config.USE_DATABASE:
        inference_id = await create_new_inference(
            evaluation_run_id=request.evaluation_run_id,

            provider=provider.name.lower(),
            model=request.model,
            temperature=request.temperature,
            messages=request.messages
        )

    response = await provider.inference(
        model_name=request.model,
        temperature=request.temperature,
        messages=request.messages,
        tool_mode=request.tool_mode,
        tools=request.tools
    )

    if config.USE_DATABASE:
        await update_inference_by_id(
            inference_id=inference_id,

            status_code=response.status_code,
            response=response.content if response.status_code == 200 else response.error_message,
            num_input_tokens=response.num_input_tokens,
            num_output_tokens=response.num_output_tokens,
            cost_usd=response.cost_usd
        )

        cost_hash_map.add_cost(request.evaluation_run_id, response.cost_usd)
    
    return response.response



# NOTE ADAM: embedding@main.py -> Handles HTTP exceptions and database
#            embedding@providers/provider.py -> Handles logging
#            embedding@providers/*.py -> Handles embedding
@app.post("/api/embedding")
@handle_http_exceptions
async def embedding(request: EmbeddingRequest) -> List[float]:
    if config.USE_DATABASE and config.CHECK_EVALUATION_RUNS:
        # Get the status of the evaluation run
        evaluation_run_status = await get_evaluation_run_status_by_id(request.evaluation_run_id)
        
        # Make sure the evaluation run actually exists
        if evaluation_run_status is None:
            raise HTTPException(
                status_code=400,
                detail=f"No evaluation run exists with the given evaluation run ID {request.evaluation_run_id}."
            )
        
        # Make sure the evaluation run is in the running_agent state
        if evaluation_run_status != EvaluationRunStatus.running_agent:
            raise HTTPException(
                status_code=400,
                detail=f"The evaluation run with ID {request.evaluation_run_id} is not in the running_agent state (current state: {evaluation_run_status.value})."
            )

        # Make sure the evaluation run has not reached or exceeded its cost limit
        cost = cost_hash_map.get_cost(request.evaluation_run_id)
        if cost >= config.MAX_COST_PER_EVALUATION_RUN_USD:
            raise HTTPException(
                status_code=429,
                detail=f"The evaluation run with ID {request.evaluation_run_id} has reached or exceeded the evaluation run cost limit of {config.MAX_COST_PER_EVALUATION_RUN_USD} USD (current cost: {cost} USD)."
            )

    

    # Make sure we support the model for embedding
    provider = get_provider_that_supports_model_for_embedding(request.model)
    if not provider:
        raise HTTPException(
            status_code=404,
            detail="The model specified is not supported by Ridges for embedding."
        )

    if config.USE_DATABASE:
        embedding_id = await create_new_embedding(
            evaluation_run_id=request.evaluation_run_id,

            provider=provider.name.lower(),
            model=request.model,
            input=request.input
        )

    response = await provider.embedding(
        model_name=request.model,
        input=request.input
    )

    if config.USE_DATABASE:
        await update_embedding_by_id(
            embedding_id=embedding_id,

            status_code=response.status_code,
            response=response.embedding if response.status_code == 200 else response.error_message,
            num_input_tokens=response.num_input_tokens,
            cost_usd=response.cost_usd
        )

        cost_hash_map.add_cost(request.evaluation_run_id, response.cost_usd)
    
    return response.response



@app.get("/debug/query-info")
async def debug_query_info():
    return get_debug_query_info()



if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)