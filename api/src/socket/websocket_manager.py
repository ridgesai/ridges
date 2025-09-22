import json
from typing import Optional, Dict, List, Union
from fastapi import WebSocket, WebSocketDisconnect

from api.src.models.evaluation import Evaluation
from loggers.logging_utils import get_logger
from api.src.backend.entities import Client
from api.src.models.validator import Validator
from api.src.models.screener import Screener
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
            self.clients: Dict[WebSocket, Client] = {}
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
        
        client_ip = websocket.client.host if websocket.client else None
        
        # Start with a base client until authentication
        client = Client(ip_address=client_ip, websocket=websocket)
        self.clients[websocket] = client
        logger.info(f"Client connected to platform socket. Total clients connected: {len(self.clients)}")
        
        try:
            while True:
                try:
                    response = await websocket.receive_text()
                    response_json = json.loads(response)
                    hotkey = getattr(self.clients[websocket], 'hotkey', None)

                    await route_message(websocket, hotkey, response_json, self.clients)
                except Exception as e:
                    import traceback
                    print(response)
                    print(e)
                    print(traceback.format_exc())
                    raise  
                
        except WebSocketDisconnect:
            client = self.clients.pop(websocket, None)
            if not client:
                return
                
            client_hotkey = getattr(client, 'hotkey', None)
            
            logger.warning(f"Client with hotkey {client_hotkey} disconnected from platform socket. Total clients connected: {len(self.clients)}. Resetting any running evaluations for this client.")

            try:
                # Only send disconnection event if we have a valid hotkey
                if client_hotkey:
                    await self.send_to_all_non_validators("validator-disconnected", { "validator_hotkey": client_hotkey })
                
                if client.get_type() == "screener":
                    logger.info(f"Screener {client_hotkey} disconnected. Handling screening disconnect.")
                    await client.disconnect()
                elif client.get_type() == "validator":
                    logger.info(f"Validator {client_hotkey} disconnected. Handling validator disconnect.")
                    await client.disconnect()
            except Exception as cleanup_error:
                logger.error(f"Error during disconnect cleanup for {client_hotkey}: {cleanup_error}")
                
        except Exception as e:
            logger.error(f"Error handling WebSocket connection: {str(e)}")
            if websocket in self.clients:
                del self.clients[websocket]
        finally:
            if websocket in self.clients:
                del self.clients[websocket]
                logger.warning(f"Had to clean up websocket in finally block")

    async def send_to_all_non_validators(self, event: str, data: dict):
        non_validators = 0
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        dead_connections = []
        
        for websocket, client in clients_snapshot.items():
            # Check if client is not an authenticated validator (no hotkey means not authenticated)
            if not hasattr(client, 'hotkey'):
                non_validators += 1
                try:
                    await websocket.send_text(json.dumps({"event": event, "data": data}))
                except Exception as e:
                    logger.warning(f"Error sending message to non-validator client: {e}")
                    # Connection is dead - mark for cleanup
                    dead_connections.append(websocket)
        
        # Clean up dead connections to prevent memory leaks
        for dead_ws in dead_connections:
            if dead_ws in self.clients:
                logger.info(f"Removing dead connection from clients during broadcast")
                del self.clients[dead_ws]
        
        logger.info(f"Platform socket broadcasted {event} to {non_validators} non-validator clients")

    async def get_clients(self):
        """Get list of connected validators and screeners"""
        clients_list = []
        
        # Create a snapshot to avoid "dictionary changed size during iteration" error
        clients_snapshot = dict(self.clients)
        for client in clients_snapshot.values():
            match client.get_type():
                case "validator":
                    validator: Validator = client
                    relative_version_num = await get_relative_version_num(validator.version_commit_hash)
                    validator_data = {
                        "type": "validator",
                        "validator_hotkey": validator.hotkey,  
                        "relative_version_num": relative_version_num,
                        "commit_hash": validator.version_commit_hash,
                        "connected_at": validator.connected_at.isoformat(),
                        "ip_address": validator.ip_address,
                        "status": validator.status,
                        "evaluating_id": validator.current_evaluation_id,
                        "evaluating_agent_hotkey": validator.current_agent_hotkey,
                        "evaluating_agent_name": validator.current_agent_name,
                        "progress": await Evaluation.get_progress(validator.current_evaluation_id) if validator.current_evaluation_id else 0
                    }
                    
                    # Always include system metrics from the validator's stored data
                    validator_data.update({
                        "cpu_percent": validator.cpu_percent,
                        "ram_percent": validator.ram_percent,
                        "ram_total_gb": validator.ram_total_gb,
                        "disk_percent": validator.disk_percent,
                        "disk_total_gb": validator.disk_total_gb,
                        "containers": validator.containers
                    })
                    
                    clients_list.append(validator_data)
                    
                case "screener":
                    screener: Screener = client
                    relative_version_num = await get_relative_version_num(screener.version_commit_hash)
                    screener_data = {
                        "type": "screener",
                        "screener_hotkey": screener.hotkey,
                        "relative_version_num": relative_version_num,
                        "commit_hash": screener.version_commit_hash,
                        "connected_at": screener.connected_at.isoformat(),
                        "ip_address": screener.ip_address,
                        "status": screener.status,
                        "screening_id": screener.screening_id,
                        "screening_agent_hotkey": screener.screening_agent_hotkey,
                        "screening_agent_name": screener.screening_agent_name,
                        "progress": await Evaluation.get_progress(screener.screening_id) if screener.screening_id else 0
                    }
                    
                    # Always include system metrics from the screener's stored data  
                    screener_data.update({
                        "cpu_percent": screener.cpu_percent,
                        "ram_percent": screener.ram_percent,
                        "ram_total_gb": screener.ram_total_gb,
                        "disk_percent": screener.disk_percent,
                        "disk_total_gb": screener.disk_total_gb,
                        "containers": screener.containers
                    })
                    
                    clients_list.append(screener_data)
                    
                case _:
                    continue

        return clients_list
    
    async def send_to_client(self, client: Client, message: Dict) -> bool:
        """Send message to specific client using Client object"""
        if client.websocket:
            try:
                await client.websocket.send_text(json.dumps(message))
                return True
            except Exception as e:
                logger.error(f"Failed to send message to client {client.ip_address}: {e}")
                return False
        return False
    
    
