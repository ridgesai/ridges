import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict
from fastapi import WebSocket, WebSocketDisconnect

from api.src.backend.queries.agents import get_agent_by_version_id
from loggers.logging_utils import get_logger
from api.src.backend.entities import ValidatorInfo
from api.src.backend.queries.evaluations import (
    cancel_screening_evaluation,
    create_evaluation,
    get_running_evaluation_by_validator_hotkey,
    reset_evaluation,
)
from api.src.socket.handlers.message_router import route_message
from api.src.socket.server_helpers import get_relative_version_num

logger = get_logger(__name__)

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
                
                # # Handle special cases for broadcasting
                # if result and response_json["event"] == "validator-info":
                #     await self.send_to_all_non_validators("validator-connected", result)
                # elif result is None and response_json["event"] == "validator-info":
                #     # Validator authentication failed, connection will be closed
                #     logger.info("Validator authentication failed, connection rejected")
                #     break
                
                # elif result and response_json["event"] == "start-evaluation":
                #     await self.send_to_all_non_validators("evaluation-started", result)
                
                # elif result and response_json["event"] == "finish-evaluation":
                #     await self.send_to_all_non_validators("evaluation-finished", result)
                    
                #     # Handle set-weights after finishing evaluation
                #     weights_result = await handle_set_weights_after_evaluation()
                #     if weights_result and "error" not in weights_result:
                #         await self.send_to_all_validators("set-weights", weights_result)
                
                # elif result and response_json["event"] == "upsert-evaluation-run":
                #     await self.send_to_all_non_validators("evaluation-run-updated", result)

        except WebSocketDisconnect:
            # CRITICAL: Remove from clients immediately to prevent memory leak
            validator_info = self.clients.pop(websocket, None)
            if not validator_info:
                return
                
            val_hotkey = validator_info.validator_hotkey
            version_commit_hash = validator_info.version_commit_hash
            
            logger.warning(f"Validator with hotkey {val_hotkey} disconnected from platform socket. Total validators connected: {len(self.clients)}. Resetting any running evaluations for this validator.")

            # Handle disconnect cleanup in a separate try-catch to not affect memory cleanup
            try:
                if val_hotkey and version_commit_hash:
                    relative_version_num = await get_relative_version_num(version_commit_hash)
                    await self.send_to_all_non_validators("validator-disconnected", {
                        "validator_hotkey": val_hotkey,
                        "relative_version_num": relative_version_num,
                        "version_commit_hash": version_commit_hash
                    })

                    evaluation = await get_running_evaluation_by_validator_hotkey(val_hotkey)
                    if evaluation:
                        if val_hotkey.startswith("i-0"):
                            logger.info(f"Screener {val_hotkey} had a running evaluation {evaluation.evaluation_id} before it disconnected. It has been cancelled.")
                            await cancel_screening_evaluation(evaluation.evaluation_id)
                        else:
                            logger.info(f"Validator {val_hotkey} had a running evaluation {evaluation.evaluation_id} before it disconnected. It has been reset to waiting.")
                            await reset_evaluation(evaluation.evaluation_id)
                    else:
                        logger.info(f"Validator {val_hotkey} did not have a running evaluation before it disconnected. No evaluations have been reset.")
            except Exception as cleanup_error:
                logger.error(f"Error during disconnect cleanup for {val_hotkey}: {cleanup_error}")
                
        except Exception as e:
            logger.error(f"Error handling WebSocket connection: {str(e)}")
            # CRITICAL: Ensure cleanup happens even if there's an unexpected error
            if websocket in self.clients:
                del self.clients[websocket]
        finally:
            # Double-check cleanup in case of any edge cases
            if websocket in self.clients:
                del self.clients[websocket]
                logger.warning(f"Had to clean up websocket in finally block")

    async def send_to_all_non_validators(self, event: str, data: dict):
        non_validators = 0
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        dead_connections = []
        
        for websocket, validator_info in clients_snapshot.items():
            # Check if client is not an authenticated validator (no validator_hotkey means not authenticated)
            if not validator_info.validator_hotkey:
                non_validators += 1
                try:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                except Exception:
                    # Connection is dead - mark for cleanup
                    dead_connections.append(websocket)
        
        # Clean up dead connections to prevent memory leaks
        for dead_ws in dead_connections:
            if dead_ws in self.clients:
                logger.info(f"Removing dead connection from clients during broadcast")
                del self.clients[dead_ws]
        
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
        dead_connections = []
        
        for websocket, validator_info in clients_snapshot.items():
            try:
                if validator_info.validator_hotkey:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                    validators += 1
            except Exception:
                # Connection is dead - mark for cleanup
                dead_connections.append(websocket)
        
        # Clean up dead connections to prevent memory leaks
        for dead_ws in dead_connections:
            if dead_ws in self.clients:
                logger.info(f"Removing dead validator connection from clients during broadcast")
                del self.clients[dead_ws]

        logger.info(f"Platform socket broadcasted {event} to {validators} validators")

    async def create_new_evaluations(self, version_id: str):
        """Create new evaluations for all connected validators"""
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        logger.debug(f"Looping through {len(clients_snapshot)} clients to create new evaluations for non-screener validators")
        for websocket, validator_info in clients_snapshot.items():
            if validator_info.validator_hotkey and not validator_info.is_screener:
                logger.debug(f"Creating new evaluation for validator {validator_info.validator_hotkey} with version ID {version_id}")
                try:
                    await create_evaluation(version_id, validator_info.validator_hotkey)

                    logger.debug(f"Attempting to send evaluation-available event to validator {validator_info.validator_hotkey}")
                    await websocket.send_text(json.dumps({"event": "evaluation-available"}))
                    logger.debug(f"Successfully sent evaluation-available event to validator {validator_info.validator_hotkey}")
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
    
    async def get_available_screener(self) -> str:
        """Get the first available screener from the connected clients"""
        logger.debug(f"Looping through {len(self.clients)} clients to find an available screener...")
        for websocket, validator_info in self.clients.items():
            if validator_info.validator_hotkey and validator_info.is_screener and validator_info.status == "available":
                logger.debug(f"Found an available screener: {validator_info.validator_hotkey}.")
                return validator_info.validator_hotkey
            else:
                logger.debug(f"Client {validator_info.validator_hotkey} is not a screener or is not available.")
        logger.warning(f"A screener was requested but all screeners are currently busy.")
        return None 
    
    async def create_pre_evaluation(self, screener_hotkey: str, version_id: str) -> str:
        """Create a pre-evaluation for a specific screener hotkey"""
        logger.debug(f"Attempting to create pre-evaluation for screener {screener_hotkey} with version ID {version_id}...")
        
        # Find the websocket for the specified screener
        websocket = None
        for ws, validator_info in self.clients.items():
            if validator_info.validator_hotkey == screener_hotkey:
                websocket = ws
                logger.debug(f"Found websocket for screener {screener_hotkey}.")
                break
        
        if not websocket:
            logger.error(f"Tried to create pre-evaluation for screener {screener_hotkey} but screener not found in connected clients")
            return None

        logger.debug(f"Attempting to get miner agent with version ID {version_id}.")
        miner_agent = await get_agent_by_version_id(version_id)
        logger.debug(f"Successfully got miner agent with version ID {version_id}.")

        try:
            evaluation = await create_evaluation(version_id, screener_hotkey)
            evaluation_id = evaluation.evaluation_id

            logger.debug(f"Attempting to send screen-agent event to screener {screener_hotkey} with evaluation ID: {evaluation_id}")
            await websocket.send_text(json.dumps({
                "event": "screen-agent",
                "evaluation_id": str(evaluation_id),
                "agent_version": miner_agent.model_dump(mode='json')
            }))
            logger.debug(f"Successfully sent screen-agent event to screener {screener_hotkey} with evaluation ID: {evaluation_id}")

            return str(evaluation_id)
        except Exception as e:
            logger.error(f"Error creating pre-evaluation for screener {screener_hotkey}: {e}")
            return None