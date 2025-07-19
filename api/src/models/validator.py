import logging
from typing import Optional, List
from datetime import datetime
from fastapi import WebSocket

from api.src.backend.entities import Client, AgentStatus
from api.src.backend.db_manager import get_db_connection, get_transaction

logger = logging.getLogger(__name__)

class Validator(Client):
    """Validator model - manages evaluations atomically"""
    
    def __init__(self, hotkey: str, websocket: WebSocket, ip_address: str, 
                 version_commit_hash: Optional[str] = None, connected_at: Optional[datetime] = None):
        super().__init__(ip_address=ip_address, websocket=websocket, connected_at=connected_at)
        self.hotkey = hotkey
        self.version_commit_hash = version_commit_hash
        self.status = "available"
        self.current_evaluation_id: Optional[str] = None
        self.current_agent_name: Optional[str] = None
    
    def get_type(self) -> str:
        return "validator"
    
    def is_available(self) -> bool:
        return self.status == "available"
    
    def set_available(self) -> None:
        """Set validator to available state"""
        self.status = "available"
        self.current_evaluation_id = None
        self.current_agent_name = None
        logger.info(f"Validator {self.hotkey}: -> available")
    
    async def start_evaluation(self, evaluation_id: str) -> None:
        """Start evaluation - update status"""
        async with get_db_connection() as conn:
            agent_name = await conn.fetchval("SELECT agent_name FROM miner_agents WHERE version_id = $1", evaluation_id)
            
        self.status = f"Evaluating agent {agent_name} with evaluation {evaluation_id}"
        self.current_evaluation_id = evaluation_id
        self.current_agent_name = agent_name
        logger.info(f"Validator {self.hotkey}: -> evaluating {agent_name}")
    
    async def connect(self):
        """Handle validator connection"""
        from api.src.models.evaluation import Evaluation
        from api.src.socket.websocket_manager import WebSocketManager
        
        self.set_available()
        
        if await Evaluation.has_waiting_for_validator(self):
            await self.send_evaluation_available(self.current_evaluation_id)
    
    async def disconnect(self):
        """Handle validator disconnection"""
        from api.src.models.evaluation import Evaluation
        await Evaluation.handle_validator_disconnection(self.hotkey)
    
    async def get_next_evaluation(self) -> Optional[str]:
        """Get next evaluation ID for this validator"""
        async with get_db_connection() as conn:
            return await conn.fetchval("""
                SELECT evaluation_id FROM evaluations 
                WHERE validator_hotkey = $1 AND status = 'waiting'
                ORDER BY created_at ASC LIMIT 1
            """, self.hotkey)
    
    async def finish_evaluation(self, evaluation_id: str, errored: bool = False, reason: Optional[str] = None):
        """Finish evaluation"""
        from api.src.models.evaluation import Evaluation
        
        evaluation = await Evaluation.get_by_id(evaluation_id)
        if not evaluation or evaluation.validator_hotkey != self.hotkey:
            return
        
        async with get_transaction() as conn:
            agent_status = await conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", evaluation.version_id)
            if AgentStatus.from_string(agent_status) != AgentStatus.evaluating:
                return
            
            if errored:
                await evaluation.error(conn, reason)
            else:
                await evaluation.finish(conn)
        
        self.set_available()
    
    async def send_evaluation_available(self, version_id: str):
        """Send evaluation available message to validator"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        await ws_manager.send_to_client(self, {"event": "evaluation-available", "version_id": version_id})
    
    @staticmethod
    async def get_connected() -> List['Validator']:
        """Get all connected validators"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        return [client for client in ws_manager.clients.values() if isinstance(client, Validator)]
