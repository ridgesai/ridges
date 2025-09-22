import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from api.src.models.screener import Screener
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from loggers.logging_utils import get_logger
from loggers.process_tracking import process_context

from api.src.utils.auth import verify_request_public
from api.src.utils.upload_agent_helpers import check_agent_banned, check_hotkey_registered, check_rate_limit, check_replay_attack, check_if_python_file, get_miner_hotkey, check_signature, check_code_similarity, check_file_size, check_agent_code, upload_agent_code_to_s3, record_upload_attempt
from api.src.backend.queries.agents import get_ban_reason
from api.src.socket.websocket_manager import WebSocketManager
from api.src.models.evaluation import Evaluation
from api.src.backend.queries.agents import get_latest_agent
from api.src.backend.entities import MinerAgent, AgentStatus
from api.src.backend.db_manager import get_transaction
from api.src.utils.agent_summary_generator import generate_and_store_agent_summary
from api.src.backend.queries.open_users import get_open_user_by_hotkey

logger = get_logger(__name__)
ws = WebSocketManager.get_instance()
open_user_password = os.getenv("OPEN_USER_PASSWORD")

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
        with process_context("handle-upload-agent") as process_id:
            logger.debug(f"Platform received a /upload/agent API request. Beginning process handle-upload-agent with process ID: {process_id}.")
            logger.info(f"Uploading agent {name} for miner {miner_hotkey}.")
            check_if_python_file(agent_file.filename)
            latest_agent: Optional[MinerAgent] = await get_latest_agent(miner_hotkey=miner_hotkey)

            agent = MinerAgent(
                version_id=str(uuid.uuid4()),
                miner_hotkey=miner_hotkey,
                agent_name=name if not latest_agent else latest_agent.agent_name,
                version_num=latest_agent.version_num + 1 if latest_agent else 0,
                created_at=datetime.now(),
                status=AgentStatus.awaiting_screening_1,
                ip_address=request.client.host if request.client else None,
            )

            if prod: await check_agent_banned(miner_hotkey=miner_hotkey)
            if prod and latest_agent: check_rate_limit(latest_agent)
            check_replay_attack(latest_agent, file_info)
            if prod: check_signature(public_key, file_info, signature)
            if prod: await check_hotkey_registered(miner_hotkey)
            file_content = await check_file_size(agent_file)
            # TODO: Uncomment this when embedding similarity check is done
            # if prod: await check_code_similarity(file_content, miner_hotkey)
            check_agent_code(file_content)

            async with Evaluation.get_lock():
                # Atomic availability check + reservation - only allow uploads if stage 1 screeners are available
                screener = await Screener.get_first_available_and_reserve(stage=1)
                if not screener:
                    logger.error(f"No available stage 1 screener for agent upload from miner {miner_hotkey}")
                    raise HTTPException(
                        status_code=503,
                        detail="No stage 1 screeners available for agent evaluation. Please try again later."
                    )

                async with get_transaction() as conn:
                    can_upload = await Evaluation.check_miner_has_no_running_evaluations(conn, miner_hotkey)
                    if not can_upload:
                        # IMPORTANT: Release screener reservation on failure
                        screener.set_available()
                        logger.error(f"Cannot upload agent for miner {miner_hotkey} - has running evaluations")
                        raise HTTPException(
                            status_code=409,
                            detail="Cannot upload new agent while previous evaluations are still running. Please wait and try again."
                        )

                    await Evaluation.replace_old_agents(conn, miner_hotkey)

                    await upload_agent_code_to_s3(agent.version_id, agent_file)

                    await conn.execute(
                        """
                        INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status, ip_address)
                        VALUES ($1, $2, $3, $4, NOW(), 'awaiting_screening_1', $5)
                    """,
                        agent.version_id,
                        agent.miner_hotkey,
                        agent.agent_name,
                        agent.version_num,
                        agent.ip_address,
                    )

                    # Create evaluation and assign to screener (commits screener state)
                    eval_id, success = await Evaluation.create_screening_and_send(conn, agent, screener)
                    if not success:
                        # If send fails, reset screener
                        if screener.status == 'reserving':
                            screener.set_available()
                            logger.warning(f"Failed to assign agent {agent.version_id} to screener")
                        else:
                            logger.warning(f"Failed to assign agent {agent.version_id} to screener - screener is not running")
                
                # Screener state is now committed, lock can be released

            # Schedule background agent summary generation
            logger.info(f"Scheduling agent summary generation for {agent.version_id}")
            background_tasks.add_task(
                generate_and_store_agent_summary,
                agent.version_id,
                run_id=f"upload-{agent.version_id}"
            )

            logger.info(f"Successfully uploaded agent {agent.version_id} for miner {miner_hotkey}.")
            logger.debug(f"Completed handle-upload-agent with process ID {process_id}.")
            
            # Record successful upload
            await record_upload_attempt(
                upload_type="agent", 
                success=True, 
                version_id=agent.version_id,
                **upload_data
            )

            return AgentUploadResponse(
                status="success",
                message=f"Successfully uploaded agent {agent.version_id} for miner {miner_hotkey}."
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

async def post_open_agent(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    open_hotkey: str = Form(..., description="Open hotkey of the open user"),
    name: str = Form(..., description="Name of the agent"),
    password: str = Form(..., description="Password to upload an open user agent"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> AgentUploadResponse:
    return AgentUploadResponse(
        status="error",
        message=f"Dashboard uploads are temporarily paused"
    )

    # Extract upload attempt data for tracking
    agent_file.file.seek(0, 2)
    file_size_bytes = agent_file.file.tell()
    agent_file.file.seek(0)
    
    upload_data = {
        'hotkey': open_hotkey,
        'agent_name': name,
        'filename': agent_file.filename,
        'file_size_bytes': file_size_bytes,
        'ip_address': getattr(request.client, 'host', None) if request.client else None
    }
    
    try:
        logger.info(f"Uploading open agent process beginning. Details: open_hotkey: {open_hotkey}, name: {name}, password: {password}")
        
        if password != open_user_password:
            logger.error(f"Someone tried to upload an open agent with an invalid password. open_hotkey: {open_hotkey}, name: {name}, password: {password}")
            raise HTTPException(status_code=401, detail="Invalid password. Fuck you.")
        
        try:
            user = await get_open_user_by_hotkey(open_hotkey)
        except Exception as e:
            logger.error(f"Error retrieving open user {open_hotkey}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error while retrieving open user")
        
        if not user:
            logger.error(f"Open user {open_hotkey} not found")
            raise HTTPException(status_code=404, detail="Open user not found. Please register an account.")

        check_if_python_file(agent_file.filename)
        latest_agent: Optional[MinerAgent] = await get_latest_agent(miner_hotkey=open_hotkey)

        agent = MinerAgent(
            version_id=str(uuid.uuid4()),
            miner_hotkey=open_hotkey,
            agent_name=name if not latest_agent else latest_agent.agent_name,
            version_num=latest_agent.version_num + 1 if latest_agent else 0,
            created_at=datetime.now(),
            status=AgentStatus.awaiting_screening,
            ip_address=request.client.host if request.client else None,
        )

        if prod: await check_agent_banned(miner_hotkey=open_hotkey)
        if prod and latest_agent: check_rate_limit(latest_agent)
        file_content = await check_file_size(agent_file)
        # TODO: Uncomment this when embedding similarity check is done
        # if prod: await check_code_similarity(file_content, open_hotkey)
        check_agent_code(file_content)

        async with Evaluation.get_lock():
            # Atomic availability check + reservation - only allow uploads if stage 1 screeners are available
            screener = await Screener.get_first_available_and_reserve(stage=1)
            if not screener:
                logger.error(f"No available stage 1 screener for agent upload from miner {open_hotkey}")
                raise HTTPException(
                    status_code=503,
                    detail="No stage 1 screeners available for agent evaluation. Please try again later."
                )

            async with get_transaction() as conn:
                can_upload = await Evaluation.check_miner_has_no_running_evaluations(conn, open_hotkey)
                if not can_upload:
                    screener.set_available()
                    logger.error(f"Cannot upload agent for miner {open_hotkey} - has running evaluations")
                    raise HTTPException(
                        status_code=409,
                        detail="Cannot upload new agent while previous evaluations are still running. Please wait and try again."
                    )

                await Evaluation.replace_old_agents(conn, open_hotkey)

                await upload_agent_code_to_s3(agent.version_id, agent_file)

                await conn.execute(
                    """
                    INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status, ip_address)
                    VALUES ($1, $2, $3, $4, NOW(), 'awaiting_screening_1', $5)
                """,
                    agent.version_id,
                    agent.miner_hotkey,
                    agent.agent_name,
                    agent.version_num,
                    agent.ip_address,
                )

                # Create evaluation and assign to screener (commits screener state)
                eval_id, success = await Evaluation.create_screening_and_send(conn, agent, screener)
                if not success:
                    # If send fails, reset screener
                    screener.set_available()
                    logger.warning(f"Failed to assign agent {agent.version_id} to screener")

        logger.info(f"Scheduling agent summary generation for {agent.version_id}")
        background_tasks.add_task(
            generate_and_store_agent_summary,
            agent.version_id,
            run_id=f"upload-{agent.version_id}"
        )

        logger.info(f"Successfully uploaded agent {agent.version_id} for open user {open_hotkey}.")
        
        # Record successful upload
        await record_upload_attempt(
            upload_type="open-agent",
            success=True,
            version_id=agent.version_id,
            **upload_data
        )

        return AgentUploadResponse(
            status="success",
            message=f"Successfully uploaded agent {agent.version_id} for open user {open_hotkey}."
        )
    
    except HTTPException as e:
        # Determine error type and get ban reason if applicable
        error_type = 'banned' if e.status_code == 403 and 'banned' in e.detail.lower() else \
                    'rate_limit' if e.status_code == 429 else 'validation_error'
        ban_reason = await get_ban_reason(open_hotkey) if error_type == 'banned' and open_hotkey else None
        
        # Record failed upload attempt
        await record_upload_attempt(
            upload_type="open-agent",
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
            upload_type="open-agent",
            success=False,
            error_type='internal_error',
            error_message=str(e),
            http_status_code=500,
            **upload_data
        )
        raise

router = APIRouter()

routes = [
    ("/agent", post_agent),
    ("/open-agent", post_open_agent),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["upload"],
        dependencies=[Depends(verify_request_public)],
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
