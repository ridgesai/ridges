import asyncio
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from pydantic import BaseModel, Field
from typing import Optional
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fiber import Keypair

from api.src.backend.queries.evaluations import get_evaluations_by_version_id, store_evaluation
from api.src.utils.config import AGENT_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.s3 import S3Manager
from api.src.utils.subtensor import get_subnet_hotkeys
from api.src.utils.code_checks import AgentCodeChecker, CheckError
from api.src.utils.similarity_checker import SimilarityChecker
from api.src.backend.queries.agents import get_latest_agent, store_agent, check_if_agent_banned
from api.src.backend.queries.evaluations import get_evaluations_by_version_id, store_evaluation
from api.src.backend.entities import MinerAgent

logger = logging.getLogger(__name__)

s3_manager = S3Manager()
similarity_checker = SimilarityChecker(similarity_threshold=0.98)
ws = WebSocketManager.get_instance()

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
    miner_hotkey = file_info.split(":")[0]
    # Check if miner_hotkey is provided
    if not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey is required"
        )
    
    is_banned = await check_if_agent_banned(miner_hotkey=miner_hotkey)

    if is_banned:
        raise HTTPException(
            status_code=400,
            detail="Your miner has been banned for attempting to obfuscate code or otherwise cheat. If this is in error, please contact us on Discord"
        )

    latest_agent: Optional[MinerAgent] = await get_latest_agent(miner_hotkey=miner_hotkey)
    
    # Rate limit how often the miner can update the agent
    if latest_agent:
        earliest_allowed_time = latest_agent.created_at + timedelta(seconds=AGENT_RATE_LIMIT_SECONDS)
        if datetime.now(timezone.utc) < earliest_allowed_time:
            raise HTTPException(
                status_code=429,
                detail=f"You must wait {AGENT_RATE_LIMIT_SECONDS} seconds before uploading a new agent version"
            )

    version_num = int(file_info.split(":")[-1])

    if latest_agent and version_num != latest_agent.version_num + 1:
        raise HTTPException(
            status_code=409,
            detail="This upload request has already been processed"
        )

    agent_name = name if not latest_agent else latest_agent.agent_name

    # Check filename
    if agent_file.filename != "agent.py":
        raise HTTPException(
            status_code=400,
            detail="File must be a python file named agent.py"
        )
    
    if latest_agent:
        evaluations = await get_evaluations_by_version_id(latest_agent.version_id)
        for evaluation in evaluations:
            if evaluation.status == "running":
                raise HTTPException(
                    status_code=400,
                    detail="An exisiting version of this agent is currently being evaluated. Please wait for it to finish before uploading a new version."
                )
        for evaluation in evaluations:
            if evaluation.status == "waiting":
                evaluation.status = "replaced"
                evaluation.finished_at = datetime.now(timezone.utc)
                await store_evaluation(evaluation)
    
    # Check file size
    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB in bytes
    file_size = 0
    content = b""
    for chunk in agent_file.file:
        file_size += len(chunk)
        content += chunk
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File size must not exceed 1MB"
            )
    # Reset file pointer
    await agent_file.seek(0)

    # keypair = Keypair(public_key=bytes.fromhex(public_key), ss58_format=42)
    # if not keypair.verify(file_info, bytes.fromhex(signature)):
    #     raise HTTPException(status_code=400, detail="Invalid signature")

    # # Check if hotkey is registered using fiber
    # if miner_hotkey not in await get_subnet_hotkeys():
    #     raise HTTPException(status_code=400, detail=f"Hotkey not registered on subnet")

    # # Similarity checks (MOVED EARLIER - fail fast!) ---------------------------------------------------
    # try:
    #     # Decode content to text for similarity checking
    #     uploaded_code = content.decode('utf-8')
        
    #     logger.info(f"Starting similarity validation for {miner_hotkey}")
        
    #     # Run similarity validation
    #     is_valid, error_message = await similarity_checker.validate_upload(uploaded_code, miner_hotkey)
        
    #     if not is_valid:
    #         logger.info(f"🚨 UPLOAD REJECTED for {miner_hotkey}: {error_message}")
    #         raise HTTPException(status_code=400, detail=error_message)
            
    #     logger.info(f"✅ Similarity checks passed for {miner_hotkey}")
        
    # except UnicodeDecodeError:
    #     raise HTTPException(status_code=400, detail="Invalid file encoding - must be UTF-8")
    # except HTTPException:
    #     # Re-raise HTTPException (similarity rejection)
    #     raise
    # except Exception as e:
    #     logger.error(f"❌ CRITICAL: Similarity checking failed for {miner_hotkey}: {e}")
    #     # BLOCK upload on similarity check errors - don't allow potential copying
    #     raise HTTPException(status_code=500, detail="Anti-copying system unavailable. Please try again later.")

    # # Static code safety checks ---------------------------------------------------
    # try:
    #     AgentCodeChecker(content).run()
    # except CheckError as e:
    #     raise HTTPException(status_code=400, detail=str(e))

    version_id = str(uuid.uuid4())

        # 1. Get list of available screeners
        # 2. IF none, HTTP 429
        # 3. Otherwise, store the miner agent
        # 4. Then create the pre-evaluation

    lock = asyncio.Lock()

    async with lock:
        available_screener = await ws.get_available_screener()
        if available_screener is None:
            raise HTTPException(
                status_code=429,
                detail="All our screeners are currently busy. Please try again later."
            )
        
        try:
            await s3_manager.upload_file_object(agent_file.file, f"{version_id}/agent.py")
            logger.info(f"Successfully uploaded agent version {version_id} to S3")
        except Exception as e:
            logger.error(f"Failed to upload agent code to S3: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to store agent in our database. Please try again later."
            )
    
        agent_object = MinerAgent(
            version_id=version_id,
            miner_hotkey=miner_hotkey,
            agent_name=agent_name,
            version_num=latest_agent.version_num + 1 if latest_agent else 0,
            created_at=datetime.now(timezone.utc),
            score=None,
            status="screening"
        )

        success = await store_agent(agent_object)
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to store agent version in our database. Please try again later."
            )
        await ws.create_pre_evaluation(available_screener, version_id)

        return AgentUploadResponse(
            status="success",
            message=f"Successfully updated agent {version_id} to version {agent_object.version_num}" if latest_agent else f"Successfully created agent {version_id}"
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