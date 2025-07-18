import asyncio
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional
from loggers.logging_utils import get_logger
import os
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.utils.upload_agent_helpers import check_agent_banned, check_hotkey_registered, check_rate_limit, check_replay_attack, check_valid_filename, get_miner_hotkey, check_signature, check_code_similarity, check_file_size, check_agent_code, upload_agent_code_to_s3
from api.src.socket.websocket_manager import WebSocketManager
from api.src.backend.queries.agents import get_latest_agent
from api.src.backend.entities import MinerAgent
from api.src.backend.agent_machine import AgentStateMachine
from fastapi import HTTPException
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
        if prod: await check_code_similarity(file_content, miner_hotkey)
        check_agent_code(file_content)

        async with lock:
            # Get an available screener for the upload
            screener = await ws.get_available_screener()
            if not screener:
                logger.error(f"No available screener for agent upload from miner {miner_hotkey}")
                raise HTTPException(
                    status_code=503,
                    detail="No screeners available for agent evaluation. Please try again later."
                )
            
            # Use state machine to upload agent with the screener
            state_machine = AgentStateMachine.get_instance()
            version_num = latest_agent.version_num + 1 if latest_agent else 0
            version_id = str(uuid.uuid4())
            await upload_agent_code_to_s3(version_id, agent_file)

            success = await state_machine.agent_upload(
                screener=screener,
                miner_hotkey=miner_hotkey,
                agent_name=name if not latest_agent else latest_agent.agent_name,
                version_num=version_num,
                version_id=version_id
            )
            
            if not success:
                logger.error(f"Failed to upload agent for miner {miner_hotkey}")
                raise HTTPException(
                    status_code=500,
                    detail="Failed to store agent version in our database. Please try again later."
                )

        logger.info(f"Successfully uploaded agent {version_id} for miner {miner_hotkey}.")
        logger.debug(f"Completed handle-upload-agent with process ID {process_id}.")

        return AgentUploadResponse(
            status="success",
            message=f"Successfully uploaded agent {version_id} for miner {miner_hotkey}."
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
            500: {"model": ErrorResponse, "description": "Internal Server Error - Server-side processing failed"},
            503: {"model": ErrorResponse, "description": "Service Unavailable - No screeners available for evaluation"}
        }
    )
