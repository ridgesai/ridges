import asyncio
import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from api.src.models.screener import Screener
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from loggers.logging_utils import get_logger
from loggers.process_tracking import process_context

from api.src.utils.auth import verify_request
from api.src.utils.upload_agent_helpers import check_agent_banned, check_hotkey_registered, check_rate_limit, check_replay_attack, check_valid_filename, get_miner_hotkey, check_signature, check_code_similarity, check_file_size, check_agent_code, upload_agent_code_to_s3
from api.src.socket.websocket_manager import WebSocketManager
from api.src.models.evaluation import Evaluation
from api.src.backend.queries.agents import get_latest_agent
from api.src.backend.entities import MinerAgent
from api.src.backend.entities import MinerAgent, AgentStatus
from api.src.backend.db_manager import get_transaction

logger = get_logger(__name__)
ws = WebSocketManager.get_instance()
upload_lock = asyncio.Lock()

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

        agent = MinerAgent(
            version_id=str(uuid.uuid4()),
            miner_hotkey=miner_hotkey,
            agent_name=name if not latest_agent else latest_agent.agent_name,
            version_num=latest_agent.version_num + 1 if latest_agent else 0,
            created_at=datetime.now(),
        )

        if prod: await check_agent_banned(miner_hotkey=miner_hotkey)
        if prod and latest_agent: check_rate_limit(latest_agent)
        check_replay_attack(latest_agent, file_info)
        if prod: check_signature(public_key, file_info, signature)
        if prod: await check_hotkey_registered(miner_hotkey)
        file_content = await check_file_size(agent_file)
        if prod: await check_code_similarity(file_content, miner_hotkey)
        check_agent_code(file_content)

        async with upload_lock:
            screener = await Screener.get_first_available()
            if not screener:
                logger.error(f"No available screener for agent upload from miner {miner_hotkey}")
                raise HTTPException(
                    status_code=503,
                    detail="No screeners available for agent evaluation. Please try again later."
                )
            
            async with get_transaction() as conn:
                can_upload = await Evaluation.check_miner_has_no_running_evaluations(conn, miner_hotkey)
                if not can_upload:
                    logger.error(f"Cannot upload agent for miner {miner_hotkey} - has running evaluations")
                    raise HTTPException(
                        status_code=409,
                        detail="Cannot upload new agent while previous evaluations are still running. Please wait and try again."
                    )

                await Evaluation.replace_old_agents(conn, miner_hotkey)

                await upload_agent_code_to_s3(agent.version_id, agent_file)

                await conn.execute(
                    """
                    INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                    VALUES ($1, $2, $3, $4, NOW(), 'awaiting_screening')
                """,
                    agent.version_id,
                    agent.miner_hotkey,
                    agent.agent_name,
                    agent.version_num,
                )

                success = await Evaluation.create_screening_and_send(conn, agent, screener)

            if not success:
                logger.error(f"Failed to send screening task to screener for miner {miner_hotkey}")
                raise HTTPException(
                    status_code=500,
                    detail="Failed to assign agent to screener. Please try again later."
                )

        logger.info(f"Successfully uploaded agent {agent.version_id} for miner {miner_hotkey}.")
        logger.debug(f"Completed handle-upload-agent with process ID {process_id}.")

        return AgentUploadResponse(
            status="success",
            message=f"Successfully uploaded agent {agent.version_id} for miner {miner_hotkey}."
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
