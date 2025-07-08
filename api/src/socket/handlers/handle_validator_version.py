import json
import os
import time
import httpx
from typing import Dict, Any
from fastapi import WebSocket

from ...utils.logging_utils import get_logger
from ...db.operations import DatabaseManager

logger = get_logger(__name__)

db = DatabaseManager()

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

async def handle_validator_version(
    websocket: WebSocket,
    clients: Dict[WebSocket, Dict[str, Any]],
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle validator-version message from a validator"""
    
    validator_hotkey = response_json["validator_hotkey"]
    version_commit_hash = response_json["version_commit_hash"]

    # Update client data directly
    if clients is not None:
        clients[websocket]["val_hotkey"] = validator_hotkey
        clients[websocket]["version_commit_hash"] = version_commit_hash
    
    logger.info(f"Validator has sent their validator version and version commit hash to the platform socket. Validator hotkey: {validator_hotkey}, Version commit hash: {version_commit_hash}")

    # Get relative version number
    relative_version_num = await get_relative_version_num(version_commit_hash)
    
    # Create evaluations for the validator
    try:
        num_evaluations_created = await db.create_evaluations_for_validator(validator_hotkey)
        logger.info(f"Created {num_evaluations_created} evaluations for newly connected validator {validator_hotkey}")
    except Exception as e:
        logger.error(f"Failed to create evaluations for validator {validator_hotkey}: {e}")
        num_evaluations_created = -1

    # Check if there's a next evaluation available
    next_evaluation = await db.get_next_evaluation(validator_hotkey)
    if next_evaluation:
        await websocket.send_text(json.dumps({"event": "evaluation-available"}))
    
    # Return data for broadcasting to non-validators
    return {
        "validator_hotkey": validator_hotkey,
        "relative_version_num": relative_version_num,
        "version_commit_hash": version_commit_hash
    } 