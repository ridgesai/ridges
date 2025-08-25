from fastapi import UploadFile, HTTPException
from typing import Optional
from api.src.socket.websocket_manager import WebSocketManager
from loggers.logging_utils import get_logger
import uuid
from datetime import datetime, timedelta, timezone

from fiber import Keypair

from api.src.backend.queries.evaluations import get_running_evaluation_by_miner_hotkey
from api.src.utils.config import AGENT_RATE_LIMIT_SECONDS
from api.src.utils.subtensor import get_subnet_hotkeys
from api.src.utils.code_checks import AgentCodeChecker, CheckError
from api.src.backend.queries.agents import check_if_agent_banned
from api.src.backend.entities import MinerAgent, Evaluation
from api.src.utils.s3 import S3Manager
from api.src.utils.similarity_checker import SimilarityChecker
from api.src.backend.db_manager import get_transaction

logger = get_logger(__name__)
s3_manager = S3Manager()
ws = WebSocketManager.get_instance()

def get_miner_hotkey(file_info: str) -> str:
    logger.debug(f"Getting miner hotkey from file info: {file_info}.")
    miner_hotkey = file_info.split(":")[0]

    if not miner_hotkey:
        logger.error(f"A miner attempted to upload an agent without a hotkey. File info: {file_info}.")
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey is required"
        )
    
    logger.debug(f"Miner hotkey successfully extracted: {miner_hotkey}.")
    return miner_hotkey

def check_if_python_file(filename: str) -> bool:
    logger.debug(f"Checking if the file is a python file...")

    if not filename.endswith(".py"):
        logger.error(f"A miner attempted to upload an agent with an invalid filename: {filename}.")
        raise HTTPException(
            status_code=400,
            detail="File must be a python file"
        )
    
    logger.debug(f"The file is a python file.")

async def check_agent_banned(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is banned...")

    if await check_if_agent_banned(miner_hotkey):
        logger.error(f"A miner attempted to upload an agent with a banned hotkey: {miner_hotkey}.")
        raise HTTPException(
            status_code=403,
            detail="Your miner hotkey has been banned for attempting to obfuscate code or otherwise cheat. If this is in error, please contact us on Discord"
        )
    
    logger.debug(f"Miner hotkey {miner_hotkey} is not banned.")

def check_rate_limit(latest_agent: MinerAgent) -> None:
    logger.debug(f"Checking if miner is rate limited...")

    earliest_allowed_time = latest_agent.created_at + timedelta(seconds=AGENT_RATE_LIMIT_SECONDS)
    logger.debug(f"Earliest allowed time: {earliest_allowed_time}. Current time: {datetime.now(timezone.utc)}. Difference: {datetime.now(timezone.utc) - earliest_allowed_time}. Minimum allowed time: {timedelta(seconds=AGENT_RATE_LIMIT_SECONDS)}.")
    
    if datetime.now(timezone.utc) < earliest_allowed_time:
        logger.error(f"A miner attempted to upload an agent too quickly. Latest agent created at {latest_agent.created_at} and current time is {datetime.now(timezone.utc)}.")
        raise HTTPException(
            status_code=429,
            detail=f"You must wait {AGENT_RATE_LIMIT_SECONDS} seconds before uploading a new agent version"
        )
    
    logger.debug(f"Miner is not rate limited.")

def check_replay_attack(latest_agent: Optional[MinerAgent], file_info: str) -> None:
    logger.debug(f"Checking if this is a replay attack...")

    version_num = int(file_info.split(":")[-1])
    logger.debug(f"Latest agent number: {latest_agent.version_num if latest_agent else None}, Attempted version number: {version_num}.")

    if latest_agent and version_num != latest_agent.version_num + 1:
        logger.error(f"A miner attempted to upload an agent with a version number that is not the next version. Latest agent version is {latest_agent.version_num} and the attempted version is {version_num}")
        raise HTTPException(
            status_code=409,
            detail="This upload request has already been processed"
        )
    
    logger.debug(f"This is not a replay attack.")

def check_signature(public_key: str, file_info: str, signature: str) -> None:
    logger.debug(f"Checking if the signature is valid...")
    logger.debug(f"Public key: {public_key}, File info: {file_info}, Signature: {signature}.")

    keypair = Keypair(public_key=bytes.fromhex(public_key), ss58_format=42)
    if not keypair.verify(file_info, bytes.fromhex(signature)):
        logger.error(f"A miner attempted to upload an agent with an invalid signature. Public key: {public_key}, File info: {file_info}, Signature: {signature}.")
        raise HTTPException(
            status_code=400, 
            detail="Invalid signature"
        )
    
    logger.debug(f"The signature is valid.")

async def check_hotkey_registered(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is registered on subnet...")

    if miner_hotkey not in await get_subnet_hotkeys():
        logger.error(f"A miner attempted to upload an agent with a hotkey that is not registered on subnet: {miner_hotkey}.")
        raise HTTPException(status_code=400, detail=f"Hotkey not registered on subnet")
    
    logger.debug(f"Miner hotkey {miner_hotkey} is registered on the subnet.")
    
async def check_file_size(agent_file: UploadFile) -> str:
    logger.debug(f"Checking if the file size is valid...")

    MAX_FILE_SIZE = 2 * 1024 * 1024 
    file_size = 0
    content = b""
    for chunk in agent_file.file:
        file_size += len(chunk)
        content += chunk
        if file_size > MAX_FILE_SIZE:
            logger.error(f"A miner attempted to upload an agent with a file size that exceeds the maximum allowed size. File size: {file_size}.")
            raise HTTPException(
                status_code=400,
                detail="File size must not exceed 1MB"
            )
    
    logger.debug(f"The file size is valid.")
    await agent_file.seek(0)
    
    # Handle both bytes and string content
    if isinstance(content, bytes):
        return content.decode('utf-8')
    else:
        return content

async def check_code_similarity(uploaded_code: str, miner_hotkey: str) -> None:
    logger.debug(f"Checking if the uploaded code is similar to the miner's previous version or top agents...")

    similarity_checker = SimilarityChecker()
    is_valid, error_msg = await similarity_checker.validate_upload(uploaded_code, miner_hotkey)

    if not is_valid:
        logger.error(error_msg)
        raise HTTPException(
            status_code=400, 
            detail=error_msg
        )

    logger.debug(f"The uploaded code is not similar to the miner's previous version or top agents.")

def check_agent_code(file_content: str) -> None:
    logger.debug(f"Checking if the agent code is valid...")

    try:
        # AgentCodeChecker expects bytes, so ensure we have bytes
        if isinstance(file_content, str):
            file_content_bytes = file_content.encode('utf-8')
        else:
            file_content_bytes = file_content  # Already bytes
        AgentCodeChecker(file_content_bytes).run()
    except CheckError as e:
        logger.error(f"A miner attempted to upload an agent with invalid code. Error: {e}.")
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
    
    logger.debug(f"The agent code is valid.")
    
async def check_eval_running_for_hotkey(miner_hotkey: str) -> None:
    logger.debug(f"Checking if an evaluation is currently running for the latest agent for miner {miner_hotkey}...")

    if await get_running_evaluation_by_miner_hotkey(miner_hotkey):
        logger.error(f"An evaluation is currently running for the latest agent for miner {miner_hotkey}.")
        raise HTTPException(
            status_code=400,
            detail="An evaluation is currently running for the latest agent for this miner. Please wait for it to finish before uploading a new version."
        )


async def upload_agent_code_to_s3(version_id: str, agent_file: UploadFile) -> None:
    logger.debug(f"Uploading agent code for version {version_id} to S3...")

    try:
        await s3_manager.upload_file_object(agent_file.file, f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Failed to upload agent code to S3. Version ID: {version_id}. Error: {e}.")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent in our database. Please try again later."
        )
    
    logger.debug(f"Successfully uploaded agent code for version {version_id} to S3.")

async def record_upload_attempt(upload_type: str, success: bool, **kwargs) -> None:
    """Record an upload attempt in the upload_attempts table."""
    try:
        async with get_transaction() as conn:
            await conn.execute(
                """INSERT INTO upload_attempts (upload_type, success, hotkey, agent_name, filename, 
                file_size_bytes, ip_address, error_type, error_message, ban_reason, http_status_code, version_id) 
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                upload_type, success, kwargs.get('hotkey'), kwargs.get('agent_name'), kwargs.get('filename'),
                kwargs.get('file_size_bytes'), kwargs.get('ip_address'), kwargs.get('error_type'),
                kwargs.get('error_message'), kwargs.get('ban_reason'), kwargs.get('http_status_code'), kwargs.get('version_id')
            )
        logger.debug(f"Recorded upload attempt: type={upload_type}, success={success}, error_type={kwargs.get('error_type')}")
    except Exception as e:
        logger.error(f"Failed to record upload attempt: {e}")


