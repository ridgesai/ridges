import random
import uvicorn
import requests
import inference_gateway.config as config
import inference_gateway.providers.chutes as chutes

from typing import List
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from models.evaluation_run import EvaluationRunStatus
from utils.database import initialize_database, deinitialize_database
from inference_gateway.models import InferenceRequest, EmbeddingRequest
from inference_gateway.ai_models import is_model_supported_for_inference
from inference_gateway.queries.evaluation_run import get_evaluation_run_status_by_id
from inference_gateway.queries.inference import get_number_of_inferences_for_evaluation_run



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
    
    yield
    
    if config.USE_DATABASE:
        await deinitialize_database()

app = FastAPI(
    title="Inference Gateway", 
    description="Inference gateway server",
    lifespan=lifespan
)



@app.post("/api/inference")
async def inference(request: InferenceRequest) -> str:
    if not config.USE_DATABASE:
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

        # TODO ADAM
        #
        # # Make sure the evaluation run has not already made too many requests
        # num_inferences = await get_number_of_inferences_for_evaluation_run(request.evaluation_run_id)
        # if num_inferences >= config.MAX_INFERENCE_REQUESTS_PER_EVALUATION_RUN:
        #     raise HTTPException(
        #         status_code=429,
        #         detail=f"The evaluation run with ID {request.evaluation_run_id} has already made too many requests (maximum is {config.MAX_INFERENCE_REQUESTS_PER_EVALUATION_RUN})."
        #     )

    # TODO ADAM
    #
    # # Make sure we support the model for inference
    # if not is_model_supported_for_inference(request.model):
    #     raise HTTPException(
    #         status_code=404,
    #         detail="The model specified is not supported by Ridges for inference."
    #     )

    return chutes.inference(request.model, request.temperature, request.messages)



@app.post("/api/embedding")
async def embedding(request: EmbeddingRequest) -> List[float]:
    try:
        response = requests.post(
            config.CHUTES_EMBEDDING_URL,
            headers={"Authorization": f"Bearer {config.CHUTES_API_KEY}"},
            json={
                "inputs": request.input,
                "seed": random.randint(0, 2 ** 32 - 1)
            }
        )
        response.raise_for_status()
        return EmbeddingResponse(response=response.json()[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)