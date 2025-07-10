import json
import os
import time
import uuid
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from api.src.utils.logging_utils import get_logger
from api.src.backend.db_manager import new_db
from api.src.backend.entities import ValidatorInfo
from api.src.backend.queries.evaluations import (
    get_running_evaluation_by_validator_hotkey,
    delete_evaluation_runs,
    store_evaluation
)
from api.src.backend.entities import Evaluation, EvaluationStatus
from api.src.socket.handlers.message_router import route_message
from api.src.socket.handlers.handle_set_weights import handle_set_weights_after_evaluation

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

class WebSocketManager:
    _instance: Optional['WebSocketManager'] = None
    _initialized: bool = False
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.clients: Dict[WebSocket, ValidatorInfo] = {}
            self._initialized = True
    
    @classmethod
    def get_instance(cls) -> 'WebSocketManager':
        """Get the singleton instance of WebSocketManager"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def handle_connection(self, websocket: WebSocket):
        """Handle a new WebSocket connection"""
        await websocket.accept()
        
        # Get client IP address
        client_ip = websocket.client.host if websocket.client else None
        
        # Add new client with empty ValidatorInfo - will be populated after authentication
        self.clients[websocket] = ValidatorInfo(ip_address=client_ip)
        logger.info(f"Client connected to platform socket. Total clients connected: {len(self.clients)}")
        
        try:
            # Keep the connection alive and wait for messages
            while True:
                # Wait for client's response
                response = await websocket.receive_text()
                response_json = json.loads(response)

                # Route message to appropriate handler
                validator_info = self.clients[websocket]
                validator_hotkey = validator_info.validator_hotkey
                    
                # Pass self.clients for validator-info, otherwise None
                result = await route_message(
                    websocket,
                    validator_hotkey,
                    response_json,
                    self.clients if response_json["event"] == "validator-info" else None
                )
                
                # Handle special cases for broadcasting
                if result and response_json["event"] == "validator-info":
                    await self.send_to_all_non_validators("validator-connected", result)
                elif result is None and response_json["event"] == "validator-info":
                    # Validator authentication failed, connection will be closed
                    logger.info("Validator authentication failed, connection rejected")
                    break
                
                elif result and response_json["event"] == "start-evaluation":
                    await self.send_to_all_non_validators("evaluation-started", result)
                
                elif result and response_json["event"] == "finish-evaluation":
                    await self.send_to_all_non_validators("evaluation-finished", result)
                    
                    # Handle set-weights after finishing evaluation
                    weights_result = await handle_set_weights_after_evaluation()
                    if weights_result and "error" not in weights_result:
                        await self.send_to_all_validators("set-weights", weights_result)
                
                elif result and response_json["event"] == "upsert-evaluation-run":
                    await self.send_to_all_non_validators("evaluation-run-updated", result)

        except WebSocketDisconnect:
            validator_info = self.clients.get(websocket)
            if not validator_info:
                return
                
            val_hotkey = validator_info.validator_hotkey
            version_commit_hash = validator_info.version_commit_hash
            relative_version_num = await get_relative_version_num(version_commit_hash) if version_commit_hash else None
            
            logger.warning(f"Validator with hotkey {val_hotkey} disconnected from platform socket. Total validators connected: {len(self.clients) - 1}. Resetting any running evaluations for this validator.")

            if val_hotkey and version_commit_hash:
                await self.send_to_all_non_validators("validator-disconnected", {
                    "validator_hotkey": val_hotkey,
                    "relative_version_num": relative_version_num,
                    "version_commit_hash": version_commit_hash
                })

                evaluation = await get_running_evaluation_by_validator_hotkey(val_hotkey)
                if evaluation:
                    # Delete all associated evaluation runs first
                    await delete_evaluation_runs(evaluation.evaluation_id)
                    logger.info(f"Deleted evaluation runs for evaluation {evaluation.evaluation_id}")
                    
                    # Reset the evaluation to waiting status
                    evaluation.status = EvaluationStatus.waiting
                    evaluation.started_at = None
                    await store_evaluation(evaluation)
                    logger.info(f"Validator {val_hotkey} had a running evaluation {evaluation.evaluation_id} before it disconnected. It has been reset to waiting.")
                else:
                    logger.info(f"Validator {val_hotkey} did not have a running evaluation before it disconnected. No evaluations have been reset.")
        except Exception as e:
            logger.error(f"Error handling WebSocket connection: {str(e)}")
        finally:
            if websocket in self.clients:
                del self.clients[websocket]

    async def send_to_all_non_validators(self, event: str, data: dict):
        non_validators = 0
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        for websocket, validator_info in clients_snapshot.items():
            # Check if client is not an authenticated validator (no validator_hotkey means not authenticated)
            if not validator_info.validator_hotkey:
                non_validators += 1
                try:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                except Exception:
                    pass
        
        logger.info(f"Platform socket broadcasted {event} to {non_validators} non-validator clients")

    async def send_to_all_validators(self, event: str, data: dict):
        """Broadcast an event to every connected validator.

        Entries in ``self.clients`` should map websocket → metadata dict, but we
        defensively skip any rows that are not dicts (e.g. a stray string) so a
        malformed client cannot break the entire broadcast.
        """

        validators = 0

        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        for websocket, validator_info in clients_snapshot.items():
            try:
                if validator_info.validator_hotkey:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                    validators += 1
            except Exception:
                pass

        logger.info(f"Platform socket broadcasted {event} to {validators} validators")

    async def create_new_evaluations(self, version_id: str):
        """Create new evaluations for all connected validators"""
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        for websocket, validator_info in clients_snapshot.items():
            if validator_info.validator_hotkey:
                try:
                    evaluation = Evaluation(
                        evaluation_id=str(uuid.uuid4()),
                        version_id=version_id,
                        validator_hotkey=validator_info.validator_hotkey,
                        status=EvaluationStatus.waiting,
                        terminated_reason=None,
                        created_at=datetime.now(timezone.utc),
                        started_at=None,
                        finished_at=None,
                        score=None
                    )
                    await store_evaluation(evaluation)
                    await websocket.send_text(json.dumps({"event": "evaluation-available"}))
                except Exception:
                    pass
    

        
    async def get_connected_validators(self):
        """Get list of connected validators"""
        validators = []
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        for websocket, validator_info in clients_snapshot.items():
            if validator_info.validator_hotkey:
                relative_version_num = await get_relative_version_num(validator_info.version_commit_hash) if validator_info.version_commit_hash else None
                validators.append({
                    "validator_hotkey": validator_info.validator_hotkey,
                    "relative_version_num": relative_version_num,
                    "commit_hash": validator_info.version_commit_hash,
                    "connected_at": validator_info.connected_at.isoformat(),
                    "ip_address": validator_info.ip_address
                })
        return validators 