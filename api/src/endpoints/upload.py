import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field

import utils.logger as logger
from queries.agent import create_agent, record_upload_attempt
from api.src.backend.queries.agents import get_latest_agent, get_ban_reason
from api.src.utils.auth import verify_request_public
from api.src.utils.upload_agent_helpers import get_miner_hotkey, check_if_python_file, check_agent_banned, \
    check_rate_limit, check_signature, check_hotkey_registered, check_file_size, check_agent_code
from models.agent import AgentStatus, Agent

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

router = APIRouter()

@router.post(
    "/agent",
    tags=["upload"],
    dependencies=[Depends(verify_request_public)],
    response_model=AgentUploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request - Invalid input or validation failed"},
        409: {"model": ErrorResponse, "description": "Conflict - Upload request already processed"},
        429: {"model": ErrorResponse, "description": "Too Many Requests - Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal Server Error - Server-side processing failed"},
        503: {"model": ErrorResponse, "description": "Service Unavailable - No screeners available for evaluation"}
    }
)
async def post_agent(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(..., description="File information containing miner hotkey and version number (format: hotkey:version)"),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> AgentUploadResponse:
    """
    Upload a new agent version for evaluation
    
    This endpoint allows miners to upload their agent code for evaluation. The agent must:
    - Be a Python file
    - Be under 1MB in size
    - Pass static code safety checks
    - Pass similarity validation to prevent copying
    - Be properly signed with the miner's keypair
    
    Rate limiting may apply based on configuration.
    """

    # Extract upload attempt data for tracking
    miner_hotkey = get_miner_hotkey(file_info)
    agent_file.file.seek(0, 2)
    file_size_bytes = agent_file.file.tell()
    agent_file.file.seek(0)
    
    upload_data = {
        'hotkey': miner_hotkey,
        'agent_name': name,
        'filename': agent_file.filename,
        'file_size_bytes': file_size_bytes,
        'ip_address': getattr(request.client, 'host', None) if request.client else None
    }
    
    try:
        logger.debug(f"Platform received a /upload/agent API request. Beginning process handle-upload-agent.")
        logger.info(f"Uploading agent {name} for miner {miner_hotkey}.")
        check_if_python_file(agent_file.filename)
        latest_agent: Optional[Agent] = await get_latest_agent(miner_hotkey=miner_hotkey)

        agent = Agent(
            agent_id=uuid.uuid4(),
            miner_hotkey=miner_hotkey,
            name=name if not latest_agent else latest_agent.name,
            version_num=latest_agent.version_num + 1 if latest_agent else 0,
            created_at=datetime.now(),
            status=AgentStatus.screening_1,
            ip_address=request.client.host if request.client else None,
        )

        if prod:
            await check_agent_banned(miner_hotkey=miner_hotkey)
            if latest_agent:
                check_rate_limit(latest_agent)

        # TODO: Bring this back if needed
        # check_replay_attack(latest_agent, file_info)

        if prod:
            check_signature(public_key, file_info, signature)
            await check_hotkey_registered(miner_hotkey)
        file_content = await check_file_size(agent_file)

        # TODO: Uncomment this when embedding similarity check is done
        # if prod: await check_code_similarity(file_content, miner_hotkey)
        check_agent_code(file_content)

        agent_text = (await agent_file.read()).decode("utf-8")
        await create_agent(agent, agent_text)


        logger.info(f"Successfully uploaded agent {agent.agent_id} for miner {miner_hotkey}.")

        # Record successful upload
        await record_upload_attempt(
            upload_type="agent",
            success=True,
            agent_id=agent.agent_id,
            **upload_data
        )

        return AgentUploadResponse(
            status="success",
            message=f"Successfully uploaded agent {agent.agent_id} for miner {miner_hotkey}."
        )
    
    except HTTPException as e:
        # Determine error type and get ban reason if applicable
        error_type = 'banned' if e.status_code == 403 and 'banned' in e.detail.lower() else \
                    'rate_limit' if e.status_code == 429 else 'validation_error'
        ban_reason = await get_ban_reason(miner_hotkey) if error_type == 'banned' and miner_hotkey else None
        
        # Record failed upload attempt
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type=error_type,
            error_message=e.detail,
            ban_reason=ban_reason,
            http_status_code=e.status_code,
            **upload_data
        )
        raise
    
    except Exception as e:
        # Record internal error
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type='internal_error',
            error_message=str(e),
            http_status_code=500,
            **upload_data
        )
        raise
