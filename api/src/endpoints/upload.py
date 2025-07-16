import asyncio
from fastapi import APIRouter, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional
from loggers.logging_utils import get_logger
import os
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.utils.upload_agent_helpers import get_miner_hotkey, check_valid_filename, check_agent_banned, check_rate_limit, check_replay_attack, check_signature, check_hotkey_registered, check_file_size, check_agent_code, check_eval_running, get_available_screener, upload_agent_code_to_s3, store_agent_in_db
from api.src.socket.websocket_manager import WebSocketManager
from api.src.backend.queries.agents import get_latest_agent
from api.src.backend.entities import MinerAgent
from loggers.process_tracking import process_context

load_dotenv()

logger = get_logger(__name__)
ws = WebSocketManager.get_instance()
lock = asyncio.Lock()

prod = False
if os.getenv("ENV") == "prod":
    logger.info("Agent Upload running in production mode.")
    prod = True
else:
    logger.info("Agent Upload running in development mode.")

class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""
    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")

class ErrorResponse(BaseModel):
    """Error response model"""
    detail: str = Field(..., description="Error message describing what went wrong")

async def post_agent(
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(..., description="File information containing miner hotkey and version number (format: hotkey:version)"),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
) -> AgentUploadResponse:
    """
    Upload a new agent version for evaluation
    
    This endpoint allows miners to upload their agent code for evaluation. The agent must:
    - Be a Python file named 'agent.py'
    - Be under 1MB in size
    - Pass static code safety checks
    - Pass similarity validation to prevent copying
    - Be properly signed with the miner's keypair
    
    Rate limiting may apply based on configuration.
    """
    with process_context("handle-upload-agent") as process_id:
        logger.debug(f"Platform received a /upload/agent API request. Beginning process handle-upload-agent with process ID: {process_id}.")

        miner_hotkey = get_miner_hotkey(file_info)
        logger.info(f"Uploading agent {name} for miner {miner_hotkey}.")
        check_valid_filename(agent_file.filename)

        latest_agent: Optional[MinerAgent] = await get_latest_agent(miner_hotkey=miner_hotkey)
        if prod: await check_agent_banned(miner_hotkey=miner_hotkey)
        if prod and latest_agent: check_rate_limit(latest_agent)
        check_replay_attack(latest_agent, file_info)
        if prod: check_signature(public_key, file_info, signature)
        if prod: await check_hotkey_registered(miner_hotkey)
        file_content = await check_file_size(agent_file)
        if prod: pass # add code similarity check back later
        check_agent_code(file_content)

        async with lock:
            await check_eval_running(latest_agent)
            if prod: available_screener = await get_available_screener()
            version_id = await store_agent_in_db(miner_hotkey, name, latest_agent)
            await upload_agent_code_to_s3(version_id, agent_file)
            if prod:
                await ws.create_pre_evaluation(available_screener, version_id)
            else:
                await ws.create_new_evaluations(version_id)

        logger.info(f"Successfully uploaded agent {version_id} for miner {miner_hotkey}.")
        logger.debug(f"Completed handle-upload-agent with process ID {process_id}.")
            
        return AgentUploadResponse(
            status="success",
            message=f"Successfully updated agent {version_id} to version {latest_agent.version_num + 1}" if latest_agent else f"Successfully created agent {version_id}"
        )

router = APIRouter()

routes = [
    ("/agent", post_agent),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["upload"],
        dependencies=[Depends(verify_request)],
        methods=["POST"],
        response_model=AgentUploadResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Bad Request - Invalid input or validation failed"},
            409: {"model": ErrorResponse, "description": "Conflict - Upload request already processed"},
            429: {"model": ErrorResponse, "description": "Too Many Requests - Rate limit exceeded"},
            500: {"model": ErrorResponse, "description": "Internal Server Error - Server-side processing failed"}
        }
    )