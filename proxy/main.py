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
from proxy.providers import InferenceManager
from proxy.models import EmbeddingRequest, InferenceRequest, SandboxStatus


CHECK_COST_LIMITS = False

logger = get_logger(__name__)

# Global client instances
chutes_client = ChutesClient()  # For embeddings
inference_manager = InferenceManager()  # For inference

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown"""
    # Startup
    logger.info("Starting proxy server...")
    if ENV != 'dev' and db_manager is not None:
        await db_manager.open()
        logger.info("Database connection established")
    else:
        logger.info("Running in dev mode - skipping database connection")
    
    yield
    
    # Shutdown
    logger.info("Shutting down proxy server...")
    if ENV != 'dev' and db_manager is not None:
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
            
            if CHECK_COST_LIMITS:
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

    # Switch Kimi to Deepseek temporarily until more capacity
    if request.model in ["moonshotai/Kimi-K2-Instruct", "moonshotai/Kimi-Dev-72B"]:
        request.model = "deepseek-ai/DeepSeek-V3-0324"

    try:
        # Log only the last incoming message to avoid flooding the console
        if request.messages:
            last_msg = request.messages[-1]
            snippet = (last_msg.content[:300] + "…") if last_msg.content and len(last_msg.content) > 300 else last_msg.content
            logger.info(
                "Inference request | model=%s | run_id=%s | total_msgs=%d | last_role=%s | last_preview=%s",
                request.model,
                request.run_id,
                len(request.messages),
                last_msg.role,
                snippet,
            )
        else:
            logger.info(
                "Inference request | model=%s | run_id=%s | total_msgs=0",
                request.model,
                request.run_id,
            )
        
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
            
            # Get inference using manager (use defaults if None)
            temperature = request.temperature if request.temperature is not None else DEFAULT_TEMPERATURE
            model = request.model if request.model is not None else DEFAULT_MODEL
            inference_result = await inference_manager.inference(
                run_id=run_uuid,
                messages=request.messages,
                temperature=temperature,
                model=model
            )
        else:
            # In dev mode or when run_id is None, skip all run_id operations
            logger.info(f"Taking dev path - ENV: {ENV}, run_id: {request.run_id}")
            temperature = request.temperature if request.temperature is not None else DEFAULT_TEMPERATURE
            model = request.model if request.model is not None else DEFAULT_MODEL
            inference_result = await inference_manager.inference(
                run_id=None,
                messages=request.messages,
                temperature=temperature,
                model=model
            )
        
        # Truncate and log the first 200 chars of the response to avoid log spam
        try:
            if isinstance(inference_result, str):
                resp_preview = (inference_result[:200] + "…") if len(inference_result) > 200 else inference_result
            else:
                resp_preview = str(inference_result)[:200]
        except Exception:
            resp_preview = "<non-string response>"

        logger.info("Inference response preview (first 200 chars): %s", resp_preview)

        logger.info("Inference request completed successfully")
        
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