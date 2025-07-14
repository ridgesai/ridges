from dotenv import load_dotenv
load_dotenv("proxy/.env")

import os
from loggers.logging_utils import get_logger
import sys
import uvicorn
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException
from proxy.config import SERVER_HOST, SERVER_PORT, LOG_LEVEL, MAX_COST_PER_RUN, DEFAULT_MODEL, DEFAULT_TEMPERATURE
from proxy.database import db_manager, get_evaluation_run_by_id, get_total_inference_cost, get_total_embedding_cost
from proxy.chutes_client import ChutesClient
from proxy.models import EmbeddingRequest, InferenceRequest, SandboxStatus



logger = get_logger(__name__)

# Global chutes client instance
chutes_client = ChutesClient()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown"""
    # Startup
    logger.info("Starting proxy server...")
    await db_manager.open()
    logger.info("Database connection established")
    
    yield
    
    # Shutdown
    logger.info("Shutting down proxy server...")
    await db_manager.close()
    logger.info("Database connection closed")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return "OK"

@app.post("/agents/embedding")
async def embedding_endpoint(request: EmbeddingRequest):
    """Proxy endpoint for chutes embedding with database validation"""
    try:
        # Get evaluation run from database
        evaluation_run = await get_evaluation_run_by_id(request.run_id)
        
        if not evaluation_run:
            logger.warning(f"Embedding request for run_id {request.run_id} - evaluation run not found")
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        
        # Check if evaluation run is in the correct state
        if evaluation_run.status != SandboxStatus.sandbox_created:
            logger.warning(f"Embedding request for run_id {request.run_id} - invalid status: {evaluation_run.status}")
            raise HTTPException(
                status_code=400, 
                detail=f"Evaluation run is not in the sandbox_created state. Current status: {evaluation_run.status}"
            )
        
        # Check cost limits at FastAPI level
        run_uuid = UUID(request.run_id)
        current_cost = await get_total_embedding_cost(run_uuid)
        if current_cost > MAX_COST_PER_RUN:
            logger.warning(f"Embedding request for run_id {request.run_id} exceeded cost limit: ${current_cost:.6f}")
            raise HTTPException(
                status_code=429,
                detail=f"Agent version has reached the maximum cost ({MAX_COST_PER_RUN}) for this evaluation run. Please do not request more embeddings."
            )
        
        # Get embedding from chutes
        embedding_result = await chutes_client.embed(run_uuid, request.input)
        
        logger.info(f"Embedding request for run_id {request.run_id} completed successfully")
        return embedding_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing embedding request for run_id {request.run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get embedding due to internal server error. Please try again later."
        )

@app.post("/agents/inference")
async def inference_endpoint(request: InferenceRequest):
    """Proxy endpoint for chutes inference with database validation"""
    try:
        # Get evaluation run from database
        evaluation_run = await get_evaluation_run_by_id(request.run_id)
        
        if not evaluation_run:
            logger.warning(f"Inference request for run_id {request.run_id} - evaluation run not found")
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        
        # Check if evaluation run is in the correct state
        if evaluation_run.status != SandboxStatus.sandbox_created:
            logger.warning(f"Inference request for run_id {request.run_id} - invalid status: {evaluation_run.status}")
            raise HTTPException(
                status_code=400,
                detail=f"Evaluation run is not in the sandbox_created state. Current status: {evaluation_run.status}"
            )
        
        # Check cost limits at FastAPI level
        run_uuid = UUID(request.run_id)
        current_cost = await get_total_inference_cost(run_uuid)
        if current_cost > MAX_COST_PER_RUN:
            logger.warning(f"Inference request for run_id {request.run_id} exceeded cost limit: ${current_cost:.6f}")
            raise HTTPException(
                status_code=429,
                detail=f"Agent version has reached the maximum cost ({MAX_COST_PER_RUN}) for this evaluation run. Please do not request more inference."
            )
        
        # Get inference from chutes (use defaults if None)
        temperature = request.temperature if request.temperature is not None else DEFAULT_TEMPERATURE
        model = request.model if request.model is not None else DEFAULT_MODEL
        inference_result = await chutes_client.inference(
            run_uuid,
            request.messages,
            temperature,
            model
        )
        
        logger.info(f"Inference request for run_id {request.run_id} completed successfully")
        return inference_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing inference request for run_id {request.run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get inference due to internal server error. Please try again later."
        )

if __name__ == "__main__":
    print(f"Starting Chutes Proxy Server on {SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT) 