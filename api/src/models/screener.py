import logging
from typing import Optional, List

from api.src.backend.entities import Client, AgentStatus, MinerAgent
from api.src.backend.db_manager import get_db_connection, get_transaction

logger = logging.getLogger(__name__)

class Screener(Client):
    """Screener model - handles screening evaluations atomically"""
    
    hotkey: str
    version_commit_hash: Optional[str] = None
    status: str = "available"
    current_evaluation_id: Optional[str] = None
    current_agent_name: Optional[str] = None
    
    def get_type(self) -> str:
        return "screener"
    
    def is_available(self) -> bool:
        return self.status == "available"
    
    def set_available(self) -> None:
        """Set screener to available state"""
        old_status = getattr(self, 'status', None)
        self.status = "available"
        self.current_evaluation_id = None
        self.current_agent_name = None
        logger.info(f"Screener {self.hotkey}: {old_status} -> available")

    async def start_screening(self, evaluation_id: str) -> bool:
        """Handle start-evaluation message"""
        from api.src.models.evaluation import Evaluation
        
        evaluation = await Evaluation.get_by_id(evaluation_id)
        if not evaluation or not evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            return False
        
        async with get_transaction() as conn:
            agent = await conn.fetchrow("SELECT status, agent_name FROM miner_agents WHERE version_id = $1", evaluation.version_id)
            agent_status = AgentStatus.from_string(agent["status"]) if agent else None
            if not agent or agent_status != AgentStatus.screening:
                logger.info(f"Screener {self.hotkey}: tried to start screening but agent is not in screening status")
                return False
            agent_name = agent["agent_name"]

            await evaluation.start(conn)
            old_status = self.status
            self.status = f"Screening agent {agent_name} with evaluation {evaluation_id}"
            self.current_evaluation_id = evaluation_id
            self.current_agent_name = agent_name
            logger.info(f"Screener {self.hotkey}: {old_status} -> screening {agent_name}")
            return True
    
    async def connect(self):
        """Handle screener connection"""
        from api.src.models.evaluation import Evaluation
        self.set_available()
        logger.info(f"Screener {self.hotkey} connected with status: {self.status}")
        await Evaluation.screen_next_awaiting_agent(self)
    
    async def disconnect(self):
        """Handle screener disconnection"""
        from api.src.models.evaluation import Evaluation
        # Explicitly reset status on disconnect to ensure clean state
        self.set_available()
        logger.info(f"Screener {self.hotkey} disconnected, status reset to: {self.status}")
        await Evaluation.handle_screener_disconnection(self.hotkey)
    
    async def finish_screening(self, evaluation_id: str, errored: bool = False, reason: Optional[str] = None):
        """Finish screening evaluation"""
        from api.src.models.evaluation import Evaluation
        
        evaluation = await Evaluation.get_by_id(evaluation_id)
        if not evaluation or not evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            return
        
        async with get_transaction() as conn:
            agent_status = await conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", evaluation.version_id)
            if AgentStatus.from_string(agent_status) != AgentStatus.screening:
                return
            
            if errored:
                await evaluation.error(conn, reason)
            else:
                await evaluation.finish(conn)
        
        self.set_available()
        await Evaluation.screen_next_awaiting_agent(self)
    
    @staticmethod
    async def get_first_available() -> Optional['Screener']:
        """Get first available screener"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        logger.debug(f"Looping through {len(ws_manager.clients)} clients to find an available screener...")
        for client in ws_manager.clients.values():
            if client.get_type() == "screener" and client.status == "available":
                logger.debug(f"Found an available screener: {client.hotkey}.")
                return client
        logger.warning(f"A screener was requested but all screeners are currently busy.")
        return None
    
    @staticmethod
    async def re_evaluate_approved_agents() -> List[MinerAgent]:
        """Re-evaluate all approved agents"""
        from api.src.socket.websocket_manager import WebSocketManager
        
        async with get_transaction() as conn:
            # Reset approved agents to awaiting screening
            agent_data = await conn.fetch("""
                UPDATE miner_agents SET status = 'awaiting_screening'
                WHERE version_id IN (SELECT version_id FROM approved_version_ids) AND status != 'replaced'
                RETURNING *
            """)
            
            agents = [MinerAgent(**agent) for agent in agent_data]

        while screener := Screener.get_first_available():
            await screener.connect()
        
        logger.info(f"Reset {len(agents)} approved agents to awaiting_screening")
        return agents 