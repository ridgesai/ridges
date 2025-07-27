import logging
from typing import Optional, List
from datetime import datetime
from fastapi import WebSocket

from api.src.backend.entities import Client, AgentStatus
from api.src.backend.db_manager import get_db_connection, get_transaction
from api.src.backend.queries.agents import get_agent_by_version_id

logger = logging.getLogger(__name__)

class Validator(Client):
    """Validator model - manages evaluations atomically"""
    
    hotkey: str
    version_commit_hash: Optional[str] = None
    status: str = "available"
    current_evaluation_id: Optional[str] = None
    current_agent_name: Optional[str] = None
    
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
    
    async def start_evaluation_and_send(self, evaluation_id: str) -> bool:
        """Start evaluation - update status"""
        if not self.is_available():
            logger.info(f"Validator {self.hotkey}: -> not available")
            return False
        # comment out for now, idfk
        
        from api.src.models.evaluation import Evaluation
        evaluation = await Evaluation.get_by_id(evaluation_id)
        
        if not evaluation or evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            return False

        miner_agent = await get_agent_by_version_id(evaluation.version_id)

        async with get_db_connection() as conn:
            evaluation_runs = await evaluation.start(conn)

        message = {
            "event": "evaluation",
            "evaluation_id": str(evaluation_id),
            "agent_version": miner_agent.model_dump(mode='json'),
            "evaluation_runs": [run.model_dump(mode='json') for run in evaluation_runs]
        }
        await self.websocket.send_json(message)
            
        self.status = f"Evaluating agent {miner_agent.agent_name} with evaluation {evaluation_id}"
        self.current_evaluation_id = evaluation_id
        self.current_agent_name = miner_agent.agent_name
        logger.info(f"Validator {self.hotkey}: -> evaluating {miner_agent.agent_name}")

        return True
    
    async def connect(self):
        """Handle validator connection"""
        from api.src.models.evaluation import Evaluation
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
                SELECT e.evaluation_id FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.validator_hotkey = $1 AND e.status = 'waiting'
                AND ma.status NOT IN ('screening', 'awaiting_screening')
                ORDER BY e.created_at ASC LIMIT 1
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
        await ws_manager.send_to_client(self, {"event": "evaluation-available", "version_id": str(version_id)})
    
    async def send_set_weights(self, data: dict):
        """Send set weights message to validator"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        await ws_manager.send_to_client(self, {"event": "set-weights", "data": data})
    
    @staticmethod
    async def get_connected() -> List['Validator']:
        """Get all connected validators"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        return [client for client in ws_manager.clients.values() if client.get_type() == "validator"]
