from dotenv import load_dotenv
load_dotenv("proxy/.env")

import os
from loggers.logging_utils import get_logger
import sys
import uvicorn
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from proxy.config import ENV, SERVER_HOST, SERVER_PORT, LOG_LEVEL, MAX_COST_PER_RUN, DEFAULT_MODEL, DEFAULT_TEMPERATURE, SCREENER_PASSWORD, WHITELISTED_VALIDATOR_IPS
from proxy.database import db_manager, get_evaluation_run_by_id, get_total_inference_cost, get_total_embedding_cost
from proxy.chutes_client import ChutesClient
from proxy.providers import InferenceManager
from proxy.models import EmbeddingRequest, InferenceRequest, SandboxStatus
from proxy.error_tracking import track_400_error, get_client_ip, BadRequestErrorCode, get_error_stats, get_top_error_sources
import json


CHECK_COST_LIMITS = False

logger = get_logger(__name__)

def load_ip_names():
    """Load IP name mappings from local JSON file"""
    try:
        with open('whitelist.json', 'r') as f:
            data = json.load(f)
            return data.get('names', {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

IP_NAMES = load_ip_names()

def format_ip_with_name(ip: str) -> str:
    """Format IP address with name from whitelist if available"""
    ip_name = IP_NAMES.get(ip)
    return f"{ip}({ip_name})" if ip_name else ip

# Global client instances
chutes_client = ChutesClient()  # For embeddings
inference_manager = InferenceManager()  # For inference


def check_request_auth(http_request: Request, endpoint_type: str) -> None:
    client_ip = get_client_ip(http_request)
    
    # If no whitelist is configured, allow all requests through
    if not WHITELISTED_VALIDATOR_IPS:
        return
    
    # First check: IP whitelist (if configured)
    if client_ip in WHITELISTED_VALIDATOR_IPS:
        # IP is whitelisted, allow through
        return

    # 2. Internal sandbox auto-allow (inject screener password for container requests)
    if client_ip.startswith("172.") or client_ip.startswith("192.168.") or client_ip == "sandbox-proxy":  ### <--- Change these ip's accordingly to work with your local network
        # These are typical Docker internal IP ranges
        return

    # 3. Screener password
    if SCREENER_PASSWORD:
        auth_header = http_request.headers.get("authorization") or http_request.headers.get("Authorization")
        
        if not auth_header:
            track_400_error(client_ip, BadRequestErrorCode.INVALID_SCREENER_PASSWORD)
            logger.warning(f"{endpoint_type.capitalize()} request missing Authorization header from IP {format_ip_with_name(client_ip)}")
            raise HTTPException(status_code=401, detail="Authorization header required")
        
        if not auth_header.startswith("Bearer "):
            track_400_error(client_ip, BadRequestErrorCode.INVALID_SCREENER_PASSWORD)
            logger.warning(f"{endpoint_type.capitalize()} request with invalid Authorization format from IP {format_ip_with_name(client_ip)}")
            raise HTTPException(status_code=401, detail="Authorization header must be Bearer token")
        
        token = auth_header[7:]
        if token == SCREENER_PASSWORD:
            return
        else:
            track_400_error(client_ip, BadRequestErrorCode.INVALID_SCREENER_PASSWORD)
            logger.warning(f"{endpoint_type.capitalize()} request with invalid screener password from IP {format_ip_with_name(client_ip)}")
            raise HTTPException(status_code=401, detail="Invalid screener password")
    
    # Whitelist is configured but IP not whitelisted and no valid password - unauthorized
    track_400_error(client_ip, BadRequestErrorCode.UNAUTHORIZED_IP_ADDRESS)
    logger.warning(f"{endpoint_type.capitalize()} request from unauthorized IP {format_ip_with_name(client_ip)}")
    raise HTTPException(status_code=401, detail="Unauthorized: IP not whitelisted and no valid authentication provided")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown"""
    # Startup
    logger.info("Starting proxy server...")
    
    # Check authentication configuration
    if not WHITELISTED_VALIDATOR_IPS:
        if SCREENER_PASSWORD:
            logger.info("Authentication: No IP whitelist configured - allowing all requests through (screener password available as fallback)")
        else:
            logger.info("Authentication: No IP whitelist configured - allowing all requests through")
    else:
        auth_methods = []
        if SCREENER_PASSWORD:
            auth_methods.append("screener password")
        auth_methods.append(f"IP whitelist ({len(WHITELISTED_VALIDATOR_IPS)} IPs)")
        logger.info(f"Authentication enabled: {', '.join(auth_methods)}")
    
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

@app.get("/error-stats")
async def error_stats():
    """Get 400 error statistics"""
    stats = get_error_stats()
    top_sources = get_top_error_sources()
    
    # Convert error codes to readable names
    error_code_names = {code.value: code.name for code in BadRequestErrorCode}
    
    # Format stats for readability, sorted by count descending
    formatted_stats = {}
    sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
    for (ip, code), count in sorted_stats:
        error_name = error_code_names.get(code, f"UNKNOWN_{code}")
        ip_name = IP_NAMES.get(ip)
        ip_display = f"{ip}({ip_name})" if ip_name else ip
        key = f"{ip_display}:{error_name}"
        formatted_stats[key] = count
    
    formatted_top = []
    for ip, code, count in top_sources:
        error_name = error_code_names.get(code, f"UNKNOWN_{code}")
        ip_name = IP_NAMES.get(ip)
        ip_display = f"{ip}({ip_name})" if ip_name else ip
        formatted_top.append({
            "ip": ip_display,
            "error_code": code,
            "error_name": error_name,
            "count": count
        })
    
    return {
        "total_errors": sum(stats.values()),
        "unique_ip_error_combinations": len(stats),
        "all_stats": formatted_stats,
        "top_10_sources": formatted_top
    }

@app.post("/api/embedding")
async def embedding_endpoint(request: EmbeddingRequest, http_request: Request):
    """Proxy endpoint for chutes embedding with database validation"""
    
    # Check request authentication
    check_request_auth(http_request, "embedding")
    
    try:
        if ENV != 'dev':
            # Production mode - run_id is required
            if not request.run_id:
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.EMBEDDING_MISSING_RUN_ID)
                logger.warning(f"Embedding request attempted with None run_id in production mode from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(status_code=400, detail="run_id is required in production mode")
            
            # Get evaluation run from database
            evaluation_run = await get_evaluation_run_by_id(request.run_id)
            
            if not evaluation_run:
                logger.warning(f"Embedding request for run_id {request.run_id} -- run_id not found")
                raise HTTPException(status_code=404, detail="Evaluation run not found")

            if (evaluation_run.status != SandboxStatus.sandbox_created) and (evaluation_run.status != SandboxStatus.started):
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.EMBEDDING_WRONG_STATUS)
                logger.warning(f"Embedding request for run_id {request.run_id} -- status != sandbox_created || started: {evaluation_run.status} from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(
                    status_code=400, 
                    detail=f"Evaluation run is not in the sandbox_created or started state. Current status: {evaluation_run.status}"
                )
            
            # Convert run_id to UUID (needed for database operations)
            try:
                run_uuid = UUID(request.run_id)
            except ValueError:
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.EMBEDDING_INVALID_UUID)
                logger.warning(f"Embedding request with invalid UUID format: {request.run_id} from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(status_code=400, detail="Invalid run_id format. Must be a valid UUID.")
            
            if CHECK_COST_LIMITS:
                # Check cost limits at FastAPI level
                current_cost = await get_total_embedding_cost(run_uuid)
                if current_cost > MAX_COST_PER_RUN:
                    logger.warning(f"Embedding request for run_id {request.run_id} -- (current_cost = ${current_cost:.6f}) > (max cost = ${MAX_COST_PER_RUN})")
                    raise HTTPException(
                        status_code=429,
                        detail=f"Agent version has reached the maximum cost ({MAX_COST_PER_RUN}) for this evaluation run. Please do not request more embeddings."
                    )
            
            # Get embedding from chutes
            embedding_result = await chutes_client.embed(run_uuid, request.input)
        else:
            # Dev mode - run_id is optional
            # logger.info(f"Dev mode or no run_id: skipping run_id validation for embedding request")
            embedding_result = await chutes_client.embed(None, request.input)
        
        # logger.info(f"Embedding request completed successfully")
        return embedding_result
        
    except HTTPException:
        raise
    except Exception as e:
        # More detailed error logging for debugging
        import traceback
        logger.error(f"Embedding request for run_id {request.run_id} -- error: {traceback.format_exc()}")
        
        raise HTTPException(
            status_code=500,
            detail="Failed to get embedding due to internal server error. Please try again later."
        )

@app.post("/api/inference")
async def inference_endpoint(request: InferenceRequest, http_request: Request):
    """Proxy endpoint for chutes inference with database validation"""

    # Check request authentication
    check_request_auth(http_request, "inference")

    # # Switch Kimi to Deepseek temporarily until more capacity
    # if request.model in ["moonshotai/Kimi-K2-Instruct", "moonshotai/Kimi-Dev-72B"]:
    #     request.model = "deepseek-ai/DeepSeek-V3-0324"

    try:
        # Don't log this stuff it provides no value

        # Log only the last incoming message to avoid flooding the console
        # if request.messages:
        #     last_msg = request.messages[-1]
        #     snippet = (last_msg.content[:300] + "…") if last_msg.content and len(last_msg.content) > 300 else last_msg.content
        #     logger.info(
        #         "Inference request | model=%s | run_id=%s | total_msgs=%d | last_role=%s | last_preview=%s",
        #         request.model,
        #         request.run_id,
        #         len(request.messages),
        #         last_msg.role,
        #         snippet,
        #     )
        # else:
        #     logger.info(
        #         "Inference request | model=%s | run_id=%s | total_msgs=0",
        #         request.model,
        #         request.run_id,
        #     )
        
        if ENV != 'dev':
            # Production mode - run_id is required
            if not request.run_id:
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.INFERENCE_MISSING_RUN_ID)
                logger.warning(f"Inference request attempted with None run_id in production mode from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(status_code=400, detail="run_id is required in production mode")
            
            # Get evaluation run from database
            try:
                run_uuid = UUID(request.run_id)
            except ValueError:
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.INFERENCE_INVALID_UUID)
                logger.warning(f"Inference request with invalid UUID format: {request.run_id} from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(status_code=400, detail="Invalid run_id format. Must be a valid UUID.")
            
            evaluation_run = await get_evaluation_run_by_id(request.run_id)
            
            if not evaluation_run:
                logger.warning(f"Inference request for run_id {request.run_id} -- run_id not found")
                raise HTTPException(status_code=404, detail="Evaluation run not found")
            
            # Check if evaluation run is in the correct state
            if (evaluation_run.status != SandboxStatus.sandbox_created) and (evaluation_run.status != SandboxStatus.started):
                client_ip = get_client_ip(http_request)
                track_400_error(client_ip, BadRequestErrorCode.INFERENCE_WRONG_STATUS)
                logger.warning(f"Inference request for run_id {request.run_id} -- status != sandbox_created || started: {evaluation_run.status} from IP {format_ip_with_name(client_ip)}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Evaluation run is not in the sandbox_created or started state. Current status: {evaluation_run.status}"
                )
            
            # Check cost limits at FastAPI level
            current_cost = await get_total_inference_cost(run_uuid)
            if current_cost > MAX_COST_PER_RUN:
                logger.warning(f"Inference request for run_id {request.run_id} -- (current_cost = ${current_cost:.6f}) > (max cost = ${MAX_COST_PER_RUN})")
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
            # Dev mode - run_id is optional
            # logger.info(f"Taking dev path - ENV: {ENV}, run_id: {request.run_id}")
            temperature = request.temperature if request.temperature is not None else DEFAULT_TEMPERATURE
            model = request.model if request.model is not None else DEFAULT_MODEL
            inference_result = await inference_manager.inference(
                run_id=None,
                messages=request.messages,
                temperature=temperature,
                model=model
            )
        logger.info(f"Inference result raw: {inference_result}")
        # Truncate and log the first 200 chars of the response to avoid log spam
        try:
            if isinstance(inference_result, str):
                resp_preview = (inference_result[:200] + "…") if len(inference_result) > 200 else inference_result
            else:
                resp_preview = str(inference_result)[:200]
        except Exception:
            resp_preview = "<non-string response>"

        # logger.info("Inference response preview (first 200 chars): %s", resp_preview)

        #logger.info("Inference request completed successfully")
        
        return inference_result
        
    except HTTPException:
        # logger.error(f"HTTPException in inference endpoint")
        raise
    except Exception as e:
        # More detailed error logging for debugging
        import traceback
        logger.error(f"Inference request for run_id {request.run_id} (model: {request.model}) -- error: {traceback.format_exc()}")
        
        raise HTTPException(
            status_code=500,
            detail="Failed to get inference due to internal server error. Please try again later."
        )

if __name__ == "__main__":
    print(f"Starting Chutes Proxy Server on {SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT) 
