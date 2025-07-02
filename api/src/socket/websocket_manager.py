import json
from typing import Optional, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from api.src.utils.logging_utils import get_logger
from api.src.socket.server_helpers import (
    upsert_evaluation_run, 
    get_next_evaluation, 
    get_agent_version_for_validator, 
    create_evaluation, 
    start_evaluation, 
    finish_evaluation, 
    reset_running_evaluations, 
    get_relative_version_num,
    create_evaluations_for_validator
)

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
            self.clients: Dict[WebSocket, Dict[str, Any]] = {}
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
        
        # Add new client to the set
        self.clients[websocket] = {"val_hotkey": None, "version_commit_hash": None}
        logger.info(f"Client connected to platform socket. Total clients connected: {len(self.clients)}")
        
        try:
            # Keep the connection alive and wait for messages
            while True:
                # Wait for client's response
                response = await websocket.receive_text()
                response_json = json.loads(response)

                if response_json["event"] == "validator-version":
                    self.clients[websocket]["val_hotkey"] = response_json["validator_hotkey"]
                    self.clients[websocket]["version_commit_hash"] = response_json["version_commit_hash"]
                    logger.info(f"Validator has sent their validator version and version commit hash to the platform socket. Validator hotkey: {self.clients[websocket]['val_hotkey']}, Version commit hash: {self.clients[websocket]['version_commit_hash']}")

                    relative_version_num = await get_relative_version_num(self.clients[websocket]["version_commit_hash"])
                    await self.send_to_all_non_validators("validator-connected", {
                        "validator_hotkey": self.clients[websocket]["val_hotkey"],
                        "relative_version_num": relative_version_num,
                        "version_commit_hash": self.clients[websocket]["version_commit_hash"]
                    })

                    num_evaluations_created = create_evaluations_for_validator(self.clients[websocket]["val_hotkey"])
                    logger.info(f"Created {num_evaluations_created} evaluations for newly connected validator {self.clients[websocket]['val_hotkey']}")

                    next_evaluation = get_next_evaluation(self.clients[websocket]["val_hotkey"])
                    if next_evaluation:
                        await websocket.send_text(json.dumps({"event": "evaluation-available"}))

                if response_json["event"] == "get-next-evaluation":
                    validator_hotkey = self.clients[websocket]["val_hotkey"]
                    socket_message = await self.get_next_evaluation(validator_hotkey)
                    await websocket.send_text(json.dumps(socket_message))
                    if "evaluation_id" in socket_message:
                        logger.info(f"Platform socket sent requested evaluation {socket_message['evaluation_id']} to validator with hotkey {validator_hotkey}")
                    else:
                        logger.info(f"Informed validator with hotkey {validator_hotkey} that there are no more evaluations available for it.")
                
                if response_json["event"] == "start-evaluation":
                    logger.info(f"Validator with hotkey {self.clients[websocket]['val_hotkey']} has started an evaluation {response_json['evaluation_id']}. Attempting to update the evaluation in the database.")
                    eval = start_evaluation(response_json["evaluation_id"])

                    eval_dict = eval.model_dump(mode='json')
                    await self.send_to_all_non_validators("evaluation-started", eval_dict)

                if response_json["event"] == "finish-evaluation":
                    logger.info(f"Validator with hotkey {self.clients[websocket]['val_hotkey']} has finished an evaluation {response_json['evaluation_id']}. Attempting to update the evaluation in the database.")
                    eval = finish_evaluation(response_json["evaluation_id"], response_json["errored"])

                    evaluation_dict = eval.model_dump(mode='json')
                    await self.send_to_all_non_validators("evaluation-finished", evaluation_dict)

                    # -------------------------------------------------
                    # After finishing an evaluation, determine the current
                    # subnet leader (top miner) and instruct validators to
                    # set their weights accordingly.
                    # -------------------------------------------------

                    try:
                        from api.src.db.operations import DatabaseManager

                        db = DatabaseManager()
                        top_agent = db.get_top_agent()  # returns TopAgentHotkey

                        if top_agent and top_agent.miner_hotkey:
                            await self.send_to_all_validators(
                                "set-weights",
                                {
                                    "miner_hotkey": top_agent.miner_hotkey,
                                    "version_id": top_agent.version_id,
                                    "avg_score": top_agent.avg_score,
                                },
                            )
                            logger.info(
                                f"Platform socket broadcasted set-weights for hotkey {top_agent.miner_hotkey} to validators"
                            )
                        else:
                            logger.warning("Could not determine top miner – skipping set-weights broadcast")
                    except Exception as e:
                        logger.error(f"Failed to broadcast set-weights: {e}")

                if response_json["event"] == "upsert-evaluation-run":
                    logger.info(f"Validator with hotkey {self.clients[websocket]['val_hotkey']} sent an evaluation run. Upserting evaluation run.")
                    eval_run = upsert_evaluation_run(response_json["evaluation_run"]) 

                    eval_run_dict = eval_run.model_dump(mode='json')
                    eval_run_dict["validator_hotkey"] = self.clients[websocket]["val_hotkey"]
                    await self.send_to_all_non_validators("evaluation-run-updated", eval_run_dict)

                if response_json["event"] == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "timestamp": response_json.get("timestamp")}))
                    logger.debug(f"Responded to ping from validator {self.clients[websocket]['val_hotkey']}")

        except WebSocketDisconnect:
            logger.info(f"Validator with hotkey {self.clients[websocket]['val_hotkey']} disconnected from platform socket. Total validators connected: {len(self.clients) - 1}. Resetting any running evaluations for this validator.")

            relative_version_num = await get_relative_version_num(self.clients[websocket]["version_commit_hash"])
            await self.send_to_all_non_validators("validator-disconnected", {
                "validator_hotkey": self.clients[websocket]["val_hotkey"],
                "relative_version_num": relative_version_num,
                "version_commit_hash": self.clients[websocket]["version_commit_hash"]
            })

            reset_running_evaluations(self.clients[websocket]["val_hotkey"])
        except Exception as e:
            logger.error(f"Error handling WebSocket connection: {str(e)}")
        finally:
            if websocket in self.clients:
                del self.clients[websocket]

    async def send_to_all_non_validators(self, event: str, data: dict):
        non_validators = 0
        disconnected_clients = []
        
        for websocket in self.clients.keys():
            if self.clients[websocket]["val_hotkey"] is None:
                non_validators += 1
                try:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                except Exception:
                    # Client disconnected, mark for removal
                    disconnected_clients.append(websocket)
        
        # Remove disconnected clients
        for websocket in disconnected_clients:
            if websocket in self.clients:
                del self.clients[websocket]
        
        logger.info(f"Platform socket broadcasted {event} to {non_validators} non-validator clients")

    async def send_to_all_validators(self, event: str, data: dict):
        """Broadcast an event to every connected validator.

        Entries in ``self.clients`` should map websocket → metadata dict, but we
        defensively skip any rows that are not dicts (e.g. a stray string) so a
        malformed client cannot break the entire broadcast.
        """

        validators = 0
        disconnected_clients = []

        for websocket, meta in self.clients.items():
            # Skip malformed or placeholder entries
            if not isinstance(meta, dict):
                continue

            try:
                if meta.get("val_hotkey") is not None:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                    validators += 1
            except Exception:
                # Client disconnected or send failed; mark for removal
                disconnected_clients.append(websocket)

        # Clean up any disconnected sockets
        for websocket in disconnected_clients:
            if websocket in self.clients:
                del self.clients[websocket]

        logger.info(f"Platform socket broadcasted {event} to {validators} validators")

    async def create_new_evaluations(self, version_id: str):
        """Create new evaluations for all connected validators"""
        disconnected_clients = []
        
        for websocket, client_data in self.clients.items():
            if client_data["val_hotkey"] is not None:
                try:
                    create_evaluation(version_id, client_data["val_hotkey"])
                    await websocket.send_text(json.dumps({"event": "evaluation-available"}))
                except Exception:
                    # Client disconnected, mark for removal
                    disconnected_clients.append(websocket)
        
        # Remove disconnected clients
        for websocket in disconnected_clients:
            if websocket in self.clients:
                del self.clients[websocket]
    
    async def get_next_evaluation(self, validator_hotkey: str):
        """Get the next evaluation for a validator"""
        try:
            evaluation = get_next_evaluation(validator_hotkey)
            if evaluation is None:
                return { "event": "evaluation" } # No evaluations available for this validator

            agent_version = get_agent_version_for_validator(evaluation.version_id)
            socket_message = {
                "event": "evaluation",
                "evaluation_id": evaluation.evaluation_id,
                "agent_version": {
                    "version_id": agent_version.version_id,
                    "agent_id": agent_version.agent_id,
                    "version_num": agent_version.version_num,
                    "created_at": agent_version.created_at.isoformat(),
                    "score": agent_version.score,
                    "miner_hotkey": agent_version.miner_hotkey
                }
            }
            return socket_message
        except Exception as e:
            logger.error(f"Error getting next evaluation: {str(e)}")
            return None
        
    async def get_connected_validators(self):
        """Get list of connected validators"""
        validators = []
        for websocket, client_data in self.clients.items():
            if client_data["val_hotkey"] and client_data["version_commit_hash"]:
                relative_version_num = await get_relative_version_num(client_data["version_commit_hash"])
                validators.append({
                    "validator_hotkey": client_data["val_hotkey"],
                    "relative_version_num": relative_version_num,
                    "commit_hash": client_data["version_commit_hash"]
                })
        return validators 