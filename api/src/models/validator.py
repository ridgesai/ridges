import logging
from typing import Literal, Optional, List

from api.src.backend.entities import Client, AgentStatus
from api.src.backend.db_manager import get_db_connection, get_transaction
from api.src.backend.queries.agents import get_agent_by_agent_id

logger = logging.getLogger(__name__)

class Validator(Client):
    hotkey: str
    version_commit_hash: Optional[str] = None
    status: Literal["available", "evaluating"] = "available"
    current_evaluation_id: Optional[str] = None
    current_agent_name: Optional[str] = None
    current_agent_hotkey: Optional[str] = None
    
    # System metrics
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    ram_total_gb: Optional[float] = None
    disk_percent: Optional[float] = None
    disk_total_gb: Optional[float] = None
    containers: Optional[int] = None
    
    def get_type(self) -> str:
        return "validator"
    
    def is_available(self) -> bool:
        return self.status == "available"
    
    def update_system_metrics(self, cpu_percent: Optional[float], ram_percent: Optional[float], 
                            disk_percent: Optional[float], containers: Optional[int],
                            ram_total_gb: Optional[float] = None, disk_total_gb: Optional[float] = None) -> None:
        """Update system metrics for this validator"""
        self.cpu_percent = cpu_percent
        self.ram_percent = ram_percent
        self.ram_total_gb = ram_total_gb
        self.disk_percent = disk_percent
        self.disk_total_gb = disk_total_gb
        self.containers = containers
        logger.debug(f"Updated system metrics for validator {self.hotkey}: CPU={cpu_percent}%, RAM={ram_percent}% ({ram_total_gb}GB), Disk={disk_percent}% ({disk_total_gb}GB), Containers={containers}")
    
    def _broadcast_status_change(self) -> None:
        """Broadcast status change to dashboard clients"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    async def _async_broadcast_status_change(self) -> None:
        """Async method to broadcast status change"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    def set_available(self) -> None:
        """Set validator to available state"""
        old_status = getattr(self, 'status', None)
        self.status = "available"
        self.current_evaluation_id = None
        self.current_agent_name = None
        self.current_agent_hotkey = None
        logger.info(f"Validator {self.hotkey}: {old_status} -> available")
        
        # Broadcast status change if status actually changed
        if old_status != self.status and old_status is not None:
            self._broadcast_status_change()
    
    async def start_evaluation_and_send(self, evaluation_id: str) -> bool:
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
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
                JOIN agents ma ON e.agent_id = ma.agent_id
                WHERE e.validator_hotkey = $1 AND e.status = 'waiting' AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                AND ma.status NOT IN ('screening_1', 'screening_2', 'screening_1', 'awaiting_screening_2', 'pruned')
                AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                ORDER BY e.screener_score DESC NULLS LAST, e.created_at ASC
                LIMIT 1
            """, self.hotkey)
    
    async def finish_evaluation(self, evaluation_id: str, errored: bool = False, reason: Optional[str] = None):
        """Finish evaluation and automatically look for next work"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    async def _check_and_start_next_evaluation(self):
        """Atomically check for and start next evaluation - MUST be called within lock"""
        from api.src.models.evaluation import Evaluation
        
        Evaluation.assert_lock_held()
        
        if not self.is_available():
            logger.info(f"Validator {self.hotkey} not available (status: {self.status})")
            return
        
        # Check if validator has waiting work and get next evaluation atomically
        if not await Evaluation.has_waiting_for_validator(self):
            logger.info(f"Validator {self.hotkey} has no waiting work in queue")
            return
        
        evaluation_id = await self.get_next_evaluation()
        if not evaluation_id:
            logger.warning(f"Validator {self.hotkey} has waiting work but no evaluation found - potential race condition")
            return
        
        logger.info(f"Validator {self.hotkey} found next evaluation {evaluation_id} - automatically starting")
        success = await self.start_evaluation_and_send(evaluation_id)
        if success:
            logger.info(f"✅ Validator {self.hotkey} successfully auto-started next evaluation {evaluation_id}")
        else:
            logger.warning(f"❌ Validator {self.hotkey} failed to auto-start evaluation {evaluation_id}")
    
    @staticmethod
    async def get_connected() -> List['Validator']:
        """Get all connected validators"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")