from dotenv import load_dotenv
load_dotenv("proxy/.env")

import os
from loggers.logging_utils import get_logger
import sys
import uvicorn
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException
from proxy.config import ENV, SERVER_HOST, SERVER_PORT, LOG_LEVEL, MAX_COST_PER_RUN, DEFAULT_MODEL, DEFAULT_TEMPERATURE
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
    if ENV != 'dev':
        await db_manager.open()
        logger.info("Database connection established")
    else:
        logger.info("Running in dev mode - skipping database connection")
    
    yield
    
    # Shutdown
    logger.info("Shutting down proxy server...")
    if ENV != 'dev':
        await db_manager.close()
        logger.info("Database connection closed")
    else:
        logger.info("Dev mode - no database connection to close")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return "OK"

@app.post("/agents/embedding")
async def embedding_endpoint(request: EmbeddingRequest):
    """Proxy endpoint for chutes embedding with database validation"""
    try:
        if ENV != 'dev' and request.run_id:
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
        else:
            # In dev mode or when run_id is None, skip all run_id operations
            logger.info(f"Dev mode or no run_id: skipping run_id validation for embedding request")
            embedding_result = await chutes_client.embed(None, request.input)
        
        logger.info(f"Embedding request completed successfully")
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
        logger.info(f"=== INFERENCE REQUEST DEBUG ===")
        logger.info(f"ENV value: {ENV}")
        logger.info(f"Request run_id: {request.run_id}")
        logger.info(f"Request messages length: {len(request.messages) if request.messages else 0}")
        logger.info(f"Request model: {request.model}")
        logger.info(f"Request temperature: {request.temperature}")
        
        if ENV != 'dev' and request.run_id:
            logger.info(f"Taking production path with run_id validation")
            # Get evaluation run from database
            run_uuid = UUID(request.run_id)
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
        else:
            # In dev mode or when run_id is None, skip all run_id operations
            logger.info(f"Taking dev path - ENV: {ENV}, run_id: {request.run_id}")
            temperature = request.temperature if request.temperature is not None else DEFAULT_TEMPERATURE
            model = request.model if request.model is not None else DEFAULT_MODEL
            logger.info(f"About to call chutes_client.inference with run_id=None")
            inference_result = await chutes_client.inference(
                None,
                request.messages,
                temperature,
                model
            )
        
        logger.info(f"Inference request completed successfully")
        return inference_result
        
    except HTTPException:
        logger.error(f"HTTPException in inference endpoint")
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