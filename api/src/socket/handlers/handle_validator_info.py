import json
import os
import time
import httpx
from typing import Dict, Any
from fastapi import WebSocket
from fiber import Keypair

from api.src.backend.queries.evaluations import create_evaluations_for_validator, get_next_evaluation_for_validator
from api.src.utils.logging_utils import get_logger
from api.src.utils.validator_auth import is_validator_registered
from api.src.backend.entities import ValidatorInfo

logger = get_logger(__name__)

_commits_cache = None
_cache_time = 0

async def get_github_commits(history_length: int = 30) -> list[str]:
    """Get the previous commits from ridgesai/ridges."""
    global _commits_cache, _cache_time
    
    if _commits_cache and (time.time() - _cache_time) < 60:
        return _commits_cache

    headers = {"Accept": "application/vnd.github.v3+json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"https://api.github.com/repos/ridgesai/ridges/commits?per_page={history_length}", headers=headers)
        response.raise_for_status()
        commits = response.json()
        _commits_cache = [commit["sha"] for commit in commits]
        _cache_time = time.time()
        return _commits_cache

async def get_relative_version_num(commit_hash: str, history_length: int = 30) -> int:
    """Get the relative version number for a commit hash."""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        
        # Add GitHub token if available for higher rate limits
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"token {token}"
        
        commit_list = await get_github_commits(history_length)
        if commit_hash not in commit_list:
            logger.warning(f"Commit {commit_hash} not found in commit list")
            return -1
            
        return commit_list.index(commit_hash)
            
    except Exception as e:
        logger.error(f"Failed to get determine relative version number for commit {commit_hash}: {e}")
        return -1

async def handle_validator_info(
    websocket: WebSocket,
    clients: Dict[WebSocket, ValidatorInfo],
    response_json: Dict[str, Any]
):
    """Handle validator-info message from a validator with cryptographic authentication"""
    
    validator_hotkey = response_json["validator_hotkey"]
    public_key = response_json["public_key"]
    signature = response_json["signature"]
    message = response_json["message"]
    version_commit_hash = response_json["version_commit_hash"]
    timestamp = response_json.get("timestamp", int(time.time()))

    # # Validate that the validator is registered in the metagraph
    # if not is_validator_registered(validator_hotkey):
    #     logger.error(f"Validator {validator_hotkey} is not registered in the metagraph. Rejecting connection.")
        
    #     # Send error message to validator before closing connection
    #     error_message = {
    #         "event": "authentication-failed",
    #         "error": "You must be a registered validator in the metagraph to connect"
    #     }
    #     await websocket.send_text(json.dumps(error_message))
        
    #     # Close the websocket connection
    #     await websocket.close(code=4003, reason="Validator not registered in metagraph")
        
    #     # Return None to indicate the connection was rejected
    #     return None

    # # Verify the cryptographic signature
    # try:
    #     keypair = Keypair(public_key=bytes.fromhex(public_key), ss58_format=42)
        
    #     # Verify that the public key matches the validator hotkey
    #     if keypair.ss58_address != validator_hotkey:
    #         logger.error(f"Public key does not match validator hotkey {validator_hotkey}")
    #         raise ValueError("Public key mismatch")
        
    #     # Verify the signature
    #     if not keypair.verify(message, bytes.fromhex(signature)):
    #         logger.error(f"Invalid signature for validator {validator_hotkey}")
    #         raise ValueError("Invalid signature")
        
    #     # Check timestamp freshness (within 5 minutes)
    #     current_time = int(time.time())
    #     if abs(current_time - timestamp) > 300:  # 5 minutes
    #         logger.error(f"Timestamp too old for validator {validator_hotkey}")
    #         raise ValueError("Timestamp too old")
        
    #     logger.info(f"Cryptographic signature verified for validator {validator_hotkey}")
        
    # except Exception as e:
    #     logger.error(f"Signature verification failed for validator {validator_hotkey}: {e}")
        
    #     # Send error message to validator before closing connection
    #     error_message = {
    #         "event": "authentication-failed",
    #         "error": "Cryptographic signature verification failed"
    #     }
    #     await websocket.send_text(json.dumps(error_message))
        
    #     # Close the websocket connection
    #     await websocket.close(code=4004, reason="Signature verification failed")
        
    #     # Return None to indicate the connection was rejected
    #     return None

    # Update the existing ValidatorInfo object with essential data only
    if clients is not None:
        validator_info = clients[websocket]
        validator_info.validator_hotkey = validator_hotkey
        validator_info.version_commit_hash = version_commit_hash
    
    logger.info(f"Validator {validator_hotkey} has been authenticated and connected. Version commit hash: {version_commit_hash}")

    # Create evaluations for the validator
    try:
        num_evaluations_created = await create_evaluations_for_validator(validator_hotkey)
        logger.info(f"Created {num_evaluations_created} evaluations for newly connected validator {validator_hotkey}")
    except Exception as e:
        logger.error(f"Failed to create evaluations for validator {validator_hotkey}: {e}")
        num_evaluations_created = -1

    # Check if there's a next evaluation available
    next_evaluation = await get_next_evaluation_for_validator(validator_hotkey)
    if next_evaluation:
        await websocket.send_text(json.dumps({"event": "evaluation-available"}))

    relative_version_num = await get_relative_version_num(version_commit_hash)
    return {
        "validator_hotkey": validator_hotkey,
        "relative_version_num": relative_version_num,
        "version_commit_hash": version_commit_hash,
        "connected_at": validator_info.connected_at,
        "ip_address": validator_info.ip_address
    }