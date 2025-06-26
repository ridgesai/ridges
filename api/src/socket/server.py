import asyncio
import websockets
import json
from typing import Optional

from api.src.utils.logging_utils import get_logger
from api.src.socket.server_helpers import upsert_evaluation_run, get_next_evaluation, get_agent_version_for_validator, create_evaluation, start_evaluation, finish_evaluation, reset_running_evaluations, get_relative_version_num

logger = get_logger(__name__)

class WebSocketServer:
    _instance: Optional['WebSocketServer'] = None
    _initialized: bool = False
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        if not self._initialized:
            self.host = host
            self.port = port
            self.uri = f"ws://{host}:{port}"
            self.clients: dict = {}
            self.server = None
            self._initialized = True
            asyncio.create_task(self.start())
    
    @classmethod
    def get_instance(cls) -> 'WebSocketServer':
        """Get the singleton instance of WebSocketServer"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def handle_connection(self, websocket):
        # Add new client to the set
        self.clients[websocket] = {"val_hotkey": None, "version_commit_hash": None}
        logger.info(f"Validator at {websocket.remote_address} connected to platform socket. Total validators connected: {len(self.clients)}")
        
        try:
            # Keep the connection alive and wait for messages
            while True:
                # Wait for client's response
                response = await websocket.recv()
                response_json = json.loads(response)

                if response_json["event"] == "validator-version":
                    self.clients[websocket]["val_hotkey"] = response_json["validator_hotkey"]
                    self.clients[websocket]["version_commit_hash"] = response_json["version_commit_hash"]
                    logger.info(f"Validator at {websocket.remote_address} has sent their validator version and version commit hash to the platform socket. Validator hotkey: {self.clients[websocket]['val_hotkey']}, Version commit hash: {self.clients[websocket]['version_commit_hash']}")

                    relative_version_num = get_relative_version_num(self.clients[websocket]["version_commit_hash"])
                    await self.notify_all_clients("validator-connected", {
                        "validator_hotkey": self.clients[websocket]["val_hotkey"],
                        "relative_version_num": relative_version_num,
                        "version_commit_hash": self.clients[websocket]["version_commit_hash"]
                    })

                    next_evaluation = get_next_evaluation(self.clients[websocket]["val_hotkey"])
                    if next_evaluation:
                        await websocket.send(json.dumps({"event": "evaluation-available"}))

                if response_json["event"] == "get-next-evaluation":
                    validator_hotkey = self.clients[websocket]["val_hotkey"]
                    socket_message = await self.get_next_evaluation(validator_hotkey)
                    await websocket.send(json.dumps(socket_message))
                    if "evaluation_id" in socket_message:
                        logger.info(f"Platform socket sent requested evaluation {socket_message['evaluation_id']} to validator at {websocket.remote_address} with hotkey {validator_hotkey}")
                    else:
                        logger.info(f"Informed validator at {websocket.remote_address} with hotkey {validator_hotkey} that there are no more evaluations available for it.")
                
                if response_json["event"] == "start-evaluation":
                    logger.info(f"Validator {websocket.remote_address} with hotkey {self.clients[websocket]['val_hotkey']} has started an evaluation {response_json['evaluation_id']}. Attempting to update the evaluation in the database.")
                    start_evaluation(response_json["evaluation_id"])

                if response_json["event"] == "finish-evaluation":
                    logger.info(f"Validator {websocket.remote_address} with hotkey {self.clients[websocket]['val_hotkey']} has finished an evaluation {response_json['evaluation_id']}. Attempting to update the evaluation in the database.")
                    finish_evaluation(response_json["evaluation_id"], response_json["errored"])

                if response_json["event"] == "upsert-evaluation-run":
                    logger.info(f"Validator {websocket.remote_address} with hotkey {self.clients[websocket]['val_hotkey']} sent an evaluation run. Upserting evaluation run.")
                    eval_run = upsert_evaluation_run(response_json["evaluation_run"]) 
                    if eval_run.finished_at:
                        # Convert Pydantic model to dict with datetime serialization
                        eval_run_dict = eval_run.model_dump(mode='json')
                        await self.notify_all_clients("evaluation-run-finished", eval_run_dict)

        except websockets.ConnectionClosed:
            logger.info(f"Validator at {websocket.remote_address} with hotkey {self.clients[websocket]['val_hotkey']} disconnected from platform socket. Total validators connected: {len(self.clients) - 1}. Resetting any running evaluations for this validator.")

            relative_version_num = get_relative_version_num(self.clients[websocket]["version_commit_hash"])
            await self.notify_all_clients("validator-disconnected", {
                "validator_hotkey": self.clients[websocket]["val_hotkey"],
                "relative_version_num": relative_version_num,
                "version_commit_hash": self.clients[websocket]["version_commit_hash"]
            })

            reset_running_evaluations(self.clients[websocket]["val_hotkey"])
        finally:
            # Remove client when they disconnect
            del self.clients[websocket]

    async def notify_all_clients(self, event: str, data: dict):
        for websocket in self.clients.keys():
            try:
                await websocket.send(json.dumps({"event": event, "data": data}))
            except websockets.ConnectionClosed:
                pass
        logger.info(f"Platform socket broadcasted {event} to {len(self.clients)} connected clients")

    async def create_new_evaluations(self, version_id: str):
        for websocket, client_data in self.clients.items():
            create_evaluation(version_id, client_data["val_hotkey"])
            await websocket.send(json.dumps({"event": "evaluation-available"}))
    
    async def get_next_evaluation(self, validator_hotkey: str):
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
        
    def get_connected_validators(self):
        validators = []
        for websocket, client_data in self.clients.items():
            if client_data["val_hotkey"] and client_data["version_commit_hash"]:
                relative_version_num = get_relative_version_num(client_data["version_commit_hash"])
                validators.append({
                    "validator_hotkey": client_data["val_hotkey"],
                    "relative_version_num": relative_version_num,
                    "commit_hash": client_data["version_commit_hash"]
                })
        return validators

    async def start(self):
        self.server = await websockets.serve(self.handle_connection, self.host, self.port, ping_timeout=None) # Timeout stuff is for a bug fix, look into it later
        logger.info(f"Platform socket started on {self.uri}")
        await asyncio.Future()  # run forever
