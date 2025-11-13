from datetime import datetime, timedelta, timezone

from fastapi import UploadFile, HTTPException
from bittensor_wallet.keypair import Keypair

import utils.logger as logger
from api.config import MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
from queries.agent import check_if_agent_banned
from api.src.utils.code_checks import AgentCodeChecker, CheckError
from api.src.utils.refresh_subnet_hotkeys import check_if_hotkey_is_registered
from models.agent import Agent


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

def check_if_python_file(filename: str) -> None:
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

def check_rate_limit(latest_agent: Agent) -> None:
    logger.debug(f"Checking if miner is rate limited...")

    earliest_allowed_time = latest_agent.created_at + timedelta(seconds=MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS)
    logger.debug(f"Earliest allowed time: {earliest_allowed_time}. Current time: {datetime.now(timezone.utc)}. Difference: {datetime.now(timezone.utc) - earliest_allowed_time}. Minimum allowed time: {timedelta(seconds=MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS)}.")
    
    if datetime.now(timezone.utc) < earliest_allowed_time:
        logger.error(f"A miner attempted to upload an agent too quickly. Latest agent created at {latest_agent.created_at} and current time is {datetime.now(timezone.utc)}.")
        raise HTTPException(
            status_code=429,
            detail=f"You must wait {MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS} seconds before uploading a new agent version"
        )
    
    logger.debug(f"Miner is not rate limited.")

def check_signature(public_key: str, file_info: str, signature: str) -> None:
    logger.debug(f"Checking if the signature is valid...")
    logger.debug(f"Public key: {public_key}, File info: {file_info}, Signature: {signature}.")

    keypair = Keypair(public_key=public_key)
    if not keypair.verify(file_info, bytes.fromhex(signature)):
        logger.error(f"A miner attempted to upload an agent with an invalid signature. Public key: {public_key}, File info: {file_info}, Signature: {signature}.")
        raise HTTPException(
            status_code=400, 
            detail="Invalid signature"
        )
    
    logger.debug(f"The signature is valid.")

async def check_hotkey_registered(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is registered on subnet...")

    if not check_if_hotkey_is_registered(miner_hotkey):
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

# TODO: Uncomment this once similarity check is done
# async def check_code_similarity(uploaded_code: str, miner_hotkey: str) -> None:
#     logger.debug(f"Checking if the uploaded code is similar to the miner's previous version or top agents...")
#
#     similarity_checker = SimilarityChecker()
#     is_valid, error_msg = await similarity_checker.validate_upload(uploaded_code, miner_hotkey)
#
#     if not is_valid:
#         logger.error(error_msg)
#         raise HTTPException(
#             status_code=400,
#             detail=error_msg
#         )
#
#     logger.debug(f"The uploaded code is not similar to the miner's previous version or top agents.")

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