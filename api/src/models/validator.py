import logging
from typing import Literal, Optional, List

from api.src.backend.entities import Client, AgentStatus
from api.src.backend.db_manager import get_db_connection, get_transaction
from api.src.backend.queries.agents import get_agent_by_version_id

logger = logging.getLogger(__name__)

class Validator(Client):
    hotkey: str
    version_commit_hash: Optional[str] = None
    status: Literal["available", "evaluating"] = "available"
    current_evaluation_id: Optional[str] = None
    current_agent_name: Optional[str] = None
    current_agent_hotkey: Optional[str] = None
    
    def get_type(self) -> str:
        return "validator"
    
    def is_available(self) -> bool:
        return self.status == "available"
    
    def set_available(self) -> None:
        """Set validator to available state"""
        old_status = getattr(self, 'status', None)
        self.status = "available"
        self.current_evaluation_id = None
        self.current_agent_name = None
        self.current_agent_hotkey = None
        logger.info(f"Validator {self.hotkey}: {old_status} -> available")
    
    async def start_evaluation_and_send(self, evaluation_id: str) -> bool:
        """Start evaluation and send to validator"""
        if not self.is_available():
            logger.info(f"Validator {self.hotkey} not available for evaluation {evaluation_id}")
            return False
        
        from api.src.models.evaluation import Evaluation
        evaluation = await Evaluation.get_by_id(evaluation_id)
        
        if not evaluation or evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            logger.warning(f"Validator {self.hotkey}: Invalid evaluation {evaluation_id}")
            return False

        miner_agent = await get_agent_by_version_id(evaluation.version_id)
        if not miner_agent:
            logger.error(f"Validator {self.hotkey}: Agent not found for evaluation {evaluation_id}")
            return False

        try:
            async with get_transaction() as conn:
                evaluation_runs = await evaluation.start(conn)

            message = {
                "event": "evaluation",
                "evaluation_id": str(evaluation_id),
                "agent_version": miner_agent.model_dump(mode='json'),
                "evaluation_runs": [run.model_dump(mode='json') for run in evaluation_runs]
            }
            
            # Send message to validator
            await self.websocket.send_json(message)

            # Broadcast to other clients
            from api.src.socket.websocket_manager import WebSocketManager
            ws_manager = WebSocketManager.get_instance()
            await ws_manager.send_to_all_non_validators("evaluation-started", message)
                
            # Commit validator state changes
            self.status = f"evaluating"
            self.current_evaluation_id = evaluation_id
            self.current_agent_name = miner_agent.agent_name
            self.current_agent_hotkey = miner_agent.miner_hotkey
            logger.info(f"Validator {self.hotkey} successfully started evaluating {miner_agent.agent_name}")

            return True
            
        except Exception as e:
            logger.error(f"Validator {self.hotkey}: Failed to send evaluation {evaluation_id}: {e}")
            return False
    
    async def connect(self):
        """Handle validator connection"""
        from api.src.models.evaluation import Evaluation
        logger.info(f"Validator {self.hotkey} connected")
        
        async with Evaluation.get_lock():
            self.set_available()
            logger.info(f"Validator {self.hotkey} available with status: {self.status}")
            await self._check_and_start_next_evaluation()
    
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
        """Finish evaluation and automatically look for next work"""
        from api.src.models.evaluation import Evaluation
        
        try:
            evaluation = await Evaluation.get_by_id(evaluation_id)
            if not evaluation or evaluation.validator_hotkey != self.hotkey:
                logger.warning(f"Validator {self.hotkey}: Invalid finish_evaluation call for evaluation {evaluation_id}")
                return
            
            async with get_transaction() as conn:
                agent_status = await conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", evaluation.version_id)
                if AgentStatus.from_string(agent_status) != AgentStatus.evaluating:
                    logger.warning(f"Validator {self.hotkey}: Agent {evaluation.version_id} not in evaluating status during finish")
                    return
                
                if errored:
                    await evaluation.error(conn, reason)
                    notification_targets = None
                else:
                    notification_targets = await evaluation.finish(conn)
            
            from api.src.socket.websocket_manager import WebSocketManager
            ws_manager = WebSocketManager.get_instance()
            await ws_manager.send_to_all_non_validators("evaluation-finished", {"evaluation_id": evaluation_id})
            
            logger.info(f"Validator {self.hotkey}: Successfully finished evaluation {evaluation_id}, errored={errored}")
            
            # Handle notifications AFTER transaction commits
            if notification_targets:
                # Note: Validators typically don't trigger stage transitions, but handle any notifications
                for validator in notification_targets.get("validators", []):
                    async with Evaluation.get_lock():
                        if validator.is_available():
                            success = await validator.start_evaluation_and_send(evaluation_id)
                            if success:
                                logger.info(f"Successfully assigned evaluation {evaluation_id} to validator {validator.hotkey}")
                                
        finally:
            # Single atomic reset and reassignment
            async with Evaluation.get_lock():
                self.set_available()
                logger.info(f"Validator {self.hotkey}: Reset to available and looking for next evaluation")
                await self._check_and_start_next_evaluation()
    
    async def send_set_weights(self, data: dict):
        """Send set weights message to validator"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        await ws_manager.send_to_client(self, {"event": "set-weights", "data": data})
    
    async def _check_and_start_next_evaluation(self):
        """Atomically check for and start next evaluation - MUST be called within lock"""
        from api.src.models.evaluation import Evaluation
        
        Evaluation.assert_lock_held()
        
        if not self.is_available():
            logger.info(f"Validator {self.hotkey} not available (status: {self.status})")
            return
        
        # Check if validator has waiting work and get next evaluation atomically
        if await Evaluation.has_waiting_for_validator(self):
            evaluation_id = await self.get_next_evaluation()
            if evaluation_id:
                logger.info(f"Validator {self.hotkey} found next evaluation {evaluation_id} - automatically starting")
                success = await self.start_evaluation_and_send(evaluation_id)
                if success:
                    logger.info(f"✅ Validator {self.hotkey} successfully auto-started next evaluation {evaluation_id}")
                else:
                    logger.warning(f"❌ Validator {self.hotkey} failed to auto-start evaluation {evaluation_id}")
            else:
                logger.warning(f"Validator {self.hotkey} has waiting work but no evaluation found - potential race condition")
        else:
            logger.info(f"Validator {self.hotkey} finished work - no more evaluations waiting in queue")
    
    @staticmethod
    async def get_connected() -> List['Validator']:
        """Get all connected validators"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        return [client for client in ws_manager.clients.values() if client.get_type() == "validator"]
