import random
import uvicorn
import requests
import inference_gateway.config as config

from openai import OpenAI
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from models.evaluation_run import EvaluationRunStatus
from utils.database import initialize_database, deinitialize_database
from inference_gateway.queries.evaluation_run import get_evaluation_run_status_by_id
from inference_gateway.models import InferenceRequest, InferenceResponse, EmbeddingRequest, EmbeddingResponse



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
async def inference(request: InferenceRequest) -> InferenceResponse:
    # Get the status of the evaluation run
    evaluation_run_status = await get_evaluation_run_status_by_id(request.evaluation_run_id)
    
    # Make sure the evaluation run actually exists
    if evaluation_run_status is None:
        raise HTTPException(
            status_code=400,
            detail="No evaluation run exists with the given evaluation run ID."
        )
    
    # Make sure the evaluation run is in the running_agent state
    if evaluation_run_status != EvaluationRunStatus.running_agent:
        raise HTTPException(
            status_code=400,
            detail="The evaluation run specified is not in the running_agent state."
        )

 
    return InferenceResponse(response=response.choices[0].message.content)



@app.post("/api/embedding")
async def embedding(request: EmbeddingRequest) -> EmbeddingResponse:
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