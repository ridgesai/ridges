from fastapi import UploadFile, HTTPException
from typing import Optional
from api.src.socket.websocket_manager import WebSocketManager
from loggers.logging_utils import get_logger
import uuid
from datetime import datetime, timedelta, timezone

from fiber import Keypair

from api.src.backend.queries.evaluations import get_evaluations_by_version_id
from api.src.utils.config import AGENT_RATE_LIMIT_SECONDS
from api.src.utils.subtensor import get_subnet_hotkeys
from api.src.utils.code_checks import AgentCodeChecker, CheckError
from api.src.backend.queries.agents import store_agent, check_if_agent_banned
from api.src.backend.entities import MinerAgent, Evaluation
from api.src.utils.s3 import S3Manager

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

def check_valid_filename(filename: str) -> bool:
    logger.debug(f"Checking if filename is valid...")

    if filename != "agent.py":
        logger.error(f"A miner attempted to upload an agent with an invalid filename: {filename}.")
        raise HTTPException(
            status_code=400,
            detail="File must be a python file named agent.py"
        )
    
    logger.debug(f"Filename is valid: {filename}.")

async def check_agent_banned(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is banned...")

    if await check_if_agent_banned(miner_hotkey):
        logger.error(f"A miner attempted to upload an agent with a banned hotkey: {miner_hotkey}.")
        raise HTTPException(
            status_code=400,
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

    MAX_FILE_SIZE = 1 * 1024 * 1024 
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
    return content

def check_agent_code(file_content: str) -> None:
    logger.debug(f"Checking if the agent code is valid...")

    try:
        AgentCodeChecker(file_content).run()
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
    
async def check_eval_running(latest_agent: Optional[MinerAgent]) -> list[Evaluation]:
    logger.debug(f"Checking if an evaluation is currently running for the latest agent {latest_agent.version_id if latest_agent else None}...")

    if latest_agent:
        evaluations = await get_evaluations_by_version_id(latest_agent.version_id)
        for evaluation in evaluations:
            if evaluation.status == "running":
                logger.error(f"An exisiting version of this agent is currently being evaluated. Version ID: {latest_agent.version_id}.")
                raise HTTPException(
                    status_code=400,
                    detail="An exisiting version of this agent is currently being evaluated. Please wait for it to finish before uploading a new version."
                )
        logger.debug(f"No evaluations are currently running for the latest agent {latest_agent.version_id}.")
        return [evaluation for evaluation in evaluations if evaluation.status == "waiting"]
    else:
        logger.debug(f"No latest agent found. This is the first agent uploaded by this miner.")
        return []

async def get_available_screener() -> str:
    logger.debug(f"Getting an available screener...")

    available_screener = await ws.get_available_screener()
    if available_screener is None:
        logger.error(f"No available screeners found.")
        raise HTTPException(
            status_code=429,
            detail="All our screeners are currently busy. Please try again later."
        )
    
    logger.debug(f"Available screener found: {available_screener}.")
    return available_screener

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
    
async def store_agent_in_db(miner_hotkey: str, name: str, latest_agent: Optional[MinerAgent]) -> str:
    logger.debug(f"Storing agent in database...")

    version_id = str(uuid.uuid4())
    logger.debug(f"Generated new version ID: {version_id}.")

    logger.debug(f"Creating MinerAgent object for version {version_id}...")

    agent_object = MinerAgent(
        version_id=version_id,
        miner_hotkey=miner_hotkey,
        agent_name=name if not latest_agent else latest_agent.agent_name,
        version_num=latest_agent.version_num + 1 if latest_agent else 0,
        created_at=datetime.now(timezone.utc),
        score=None,
        status="screening"
    )
    logger.debug(f"MinerAgent object created for version {version_id}.")

    logger.debug(f"Attempting to store agent {version_id} in database...")
    success = await store_agent(agent_object)
    if not success:
        logger.error(f"Failed to store agent in database. Version ID: {version_id}.")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    logger.debug(f"Successfully stored agent {version_id} in database.")
    return version_id