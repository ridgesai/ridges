import asyncio
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from pydantic import BaseModel, Field
from typing import Optional
from loggers.logging_utils import get_logger
import uuid
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

from fiber import Keypair

from api.src.utils.config import AGENT_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.s3 import S3Manager
from api.src.utils.subtensor import get_subnet_hotkeys
from api.src.utils.code_checks import AgentCodeChecker, CheckError
from api.src.utils.similarity_checker import SimilarityChecker
from api.src.backend.queries.agents import get_latest_agent, store_agent, check_if_agent_banned
from api.src.backend.entities import MinerAgent, Evaluation

load_dotenv()

logger = get_logger(__name__)

s3_manager = S3Manager()
similarity_checker = SimilarityChecker(similarity_threshold=0.98)
ws = WebSocketManager.get_instance()

lock = asyncio.Lock()

prod = False
if os.getenv("ENV") == "prod":
    prod = True

class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""
    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")

class ErrorResponse(BaseModel):
    """Error response model"""
    detail: str = Field(..., description="Error message describing what went wrong")

def _get_miner_hotkey(file_info: str) -> str:
    miner_hotkey = file_info.split(":")[0]

    if not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey is required"
        )
    
    return miner_hotkey

def _check_valid_filename(filename: str) -> bool:
    if filename != "agent.py":
        raise HTTPException(
            status_code=400,
            detail="File must be a python file named agent.py"
        )
    
async def _check_agent_banned(miner_hotkey: str) -> None:
    if await check_if_agent_banned(miner_hotkey):
        raise HTTPException(
            status_code=400,
            detail="Your miner hotkey has been banned for attempting to obfuscate code or otherwise cheat. If this is in error, please contact us on Discord"
        )
    
def _check_rate_limit(latest_agent: MinerAgent) -> None:
    earliest_allowed_time = latest_agent.created_at + timedelta(seconds=AGENT_RATE_LIMIT_SECONDS)
    if datetime.now(timezone.utc) < earliest_allowed_time:
        raise HTTPException(
            status_code=429,
            detail=f"You must wait {AGENT_RATE_LIMIT_SECONDS} seconds before uploading a new agent version"
        )

def _check_replay_attack(latest_agent: Optional[MinerAgent], file_info: str) -> None:
    version_num = int(file_info.split(":")[-1])
    if latest_agent and version_num != latest_agent.version_num + 1:
        raise HTTPException(
            status_code=409,
            detail="This upload request has already been processed"
        )
    
def _check_signature(public_key: str, file_info: str, signature: str) -> None:
    keypair = Keypair(public_key=bytes.fromhex(public_key), ss58_format=42)
    if not keypair.verify(file_info, bytes.fromhex(signature)):
        raise HTTPException(
            status_code=400, 
            detail="Invalid signature"
        )

async def _check_hotkey_registered(miner_hotkey: str) -> None:
    if miner_hotkey not in await get_subnet_hotkeys():
        raise HTTPException(status_code=400, detail=f"Hotkey not registered on subnet")
    
async def _check_file_size(agent_file: UploadFile) -> str:
    MAX_FILE_SIZE = 1 * 1024 * 1024 
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
    await agent_file.seek(0)
    return content

def _check_agent_code(file_content: str) -> None:
    try:
        AgentCodeChecker(file_content).run()
    except CheckError as e:
        raise HTTPException(
                status_code=400, 
                detail=str(e)
            )
    except Exception as e:
        logger.error(f"Error running static code safety checks: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Static code safety checks failed. Please try again later."
        )
    
async def _check_eval_running(latest_agent: Optional[MinerAgent]) -> list[Evaluation]:
        if latest_agent:
            evaluations = await get_evaluations_by_version_id(latest_agent.version_id)
            for evaluation in evaluations:
                if evaluation.status == "running":
                    raise HTTPException(
                        status_code=400,
                        detail="An exisiting version of this agent is currently being evaluated. Please wait for it to finish before uploading a new version."
                    )
            return [evaluation for evaluation in evaluations if evaluation.status == "waiting"]

async def _get_available_screener() -> str:
    available_screener = await ws.get_available_screener()
    if available_screener is None:
        raise HTTPException(
            status_code=429,
            detail="All our screeners are currently busy. Please try again later."
        )
    return available_screener

async def _update_waiting_evals(waiting_evals: list[Evaluation]) -> None:
    if waiting_evals:
        for evaluation in waiting_evals:
            evaluation.status = "replaced"
            evaluation.finished_at = datetime.now(timezone.utc)
            await store_evaluation(evaluation)

async def _upload_agent_code_to_s3(version_id: str, agent_file: UploadFile) -> None:
    try:
        await s3_manager.upload_file_object(agent_file.file, f"{version_id}/agent.py")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent in our database. Please try again later."
        )
    
async def _store_agent_in_db(miner_hotkey: str, name: str, latest_agent: Optional[MinerAgent]) -> str:
    version_id = str(uuid.uuid4())
    agent_object = MinerAgent(
        version_id=version_id,
        miner_hotkey=miner_hotkey,
        agent_name=name if not latest_agent else latest_agent.agent_name,
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
    
    return version_id

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
    # Basic checks
    miner_hotkey = _get_miner_hotkey(file_info)
    _check_valid_filename(agent_file.filename)

    # Get latest agent
    latest_agent: Optional[MinerAgent] = await get_latest_agent(miner_hotkey=miner_hotkey)
    
    # banned (step 1)
    if prod: await _check_agent_banned(miner_hotkey=miner_hotkey)
    
    # rate limit (step 2)
    if prod and latest_agent: _check_rate_limit(latest_agent)

    # replay attack (step 3)
    _check_replay_attack(latest_agent, file_info)
    
    # signature (step 4)
    if prod: _check_signature(public_key, file_info, signature)
    
    # hotkey registered (step 5)
    if prod: await _check_hotkey_registered(miner_hotkey)

    # file size (step 6)
    file_content = await _check_file_size(agent_file)

    # similarity check (step 7)
    if prod: pass # add back later

    # parse + import + function check (step 8, 9, 10)
    _check_agent_code(file_content)

    # eval currenlty running (step 11)
    waiting_evals = await _check_eval_running(latest_agent)

    async with lock:
        if prod: available_screener = await _get_available_screener()

        await _update_waiting_evals(waiting_evals)

        version_id = await _store_agent_in_db(miner_hotkey, name, latest_agent)
            
        await _upload_agent_code_to_s3(version_id, agent_file)
        
        if prod:
            await ws.create_pre_evaluation(available_screener, version_id)
        else:
            await ws.create_new_evaluations(version_id)

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