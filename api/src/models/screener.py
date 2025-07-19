import logging
from typing import Optional, List


from api.src.models.validator import Validator
from api.src.backend.entities import AgentStatus, MinerAgent
from api.src.backend.db_manager import get_db_connection, get_transaction

logger = logging.getLogger(__name__)

class Screener(Validator):
    """Screener model - handles screening evaluations atomically"""
    
    def get_type(self) -> str:
        return "screener"

    async def start_screening(self, evaluation_id: str):
        """Handle start-evaluation message"""
        from api.src.models.evaluation import Evaluation
        
        evaluation = await Evaluation.get_by_id(evaluation_id)
        if not evaluation or not evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            return
        
        async with get_transaction() as conn:
            agent = await conn.fetchrow("SELECT status, agent_name FROM miner_agents WHERE version_id = $1", evaluation.version_id)
            if not agent or AgentStatus.from_string(agent["status"]) != AgentStatus.screening:
                return
            
            await evaluation.start(conn)
            self._set_screening_status(evaluation_id, agent["agent_name"])
    
    def _set_screening_status(self, evaluation_id: str, agent_name: str) -> None:
        """Update screener status to show current screening work"""
        self.status = f"Screening agent {agent_name} with evaluation {evaluation_id}"
        self.current_evaluation_id = evaluation_id
        self.current_agent_name = agent_name
        logger.info(f"Screener {self.hotkey}: -> screening {agent_name}")
    
    async def connect(self):
        """Handle screener connection"""
        from api.src.models.evaluation import Evaluation
        self.set_available()
        await Evaluation.screen_next_awaiting_agent(self)
    
    async def disconnect(self):
        """Handle screener disconnection"""
        from api.src.models.evaluation import Evaluation
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
    def get_first_available() -> Optional['Screener']:
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