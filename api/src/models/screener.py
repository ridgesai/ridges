import logging
from typing import Literal, Optional, List
import asyncpg

from api.src.backend.entities import Client, AgentStatus, MinerAgent
from api.src.backend.db_manager import get_transaction

logger = logging.getLogger(__name__)

class Screener(Client):
    """Screener model - handles screening evaluations atomically"""
    
    hotkey: str
    version_commit_hash: Optional[str] = None
    status: Literal["available", "reserving", "screening"] = "available"
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

    @staticmethod
    def get_stage(hotkey: str) -> Optional[int]:
        """Determine screening stage based on hotkey"""
        if hotkey.startswith("screener-1-"):
            return 1
        elif hotkey.startswith("screener-2-"):
            return 2
        elif hotkey.startswith("i-0"):  # Legacy screeners are stage 1
            return 1
        else:
            return None

    @staticmethod
    async def get_combined_screener_score(conn: asyncpg.Connection, version_id: str) -> tuple[Optional[float], Optional[str]]:
        """Calculate combined screener score as (questions solved by both) / (questions asked by both)
        
        Returns:
            tuple[Optional[float], Optional[str]]: (score, error_message)
            - score: The calculated score, or None if calculation failed
            - error_message: None if successful, error description if failed
        """
        # Get evaluation IDs for both screener stages
        stage_1_eval_id = await conn.fetchval(
            """
            SELECT evaluation_id FROM evaluations 
            WHERE version_id = $1 
            AND validator_hotkey LIKE 'screener-1-%'
            AND status = 'completed'
            ORDER BY created_at DESC 
            LIMIT 1
            """,
            version_id
        )
        
        stage_2_eval_id = await conn.fetchval(
            """
            SELECT evaluation_id FROM evaluations 
            WHERE version_id = $1 
            AND validator_hotkey LIKE 'screener-2-%'
            AND status = 'completed'
            ORDER BY created_at DESC 
            LIMIT 1
            """,
            version_id
        )
        
        if not stage_1_eval_id or not stage_2_eval_id:
            missing = []
            if not stage_1_eval_id:
                missing.append("stage-1")
            if not stage_2_eval_id:
                missing.append("stage-2")
            return None, f"Missing completed screener evaluation(s): {', '.join(missing)}"
            
        # Get solved count and total count for both evaluations
        results = await conn.fetch(
            """
            SELECT 
                SUM(CASE WHEN solved THEN 1 ELSE 0 END) as solved_count,
                COUNT(*) as total_count
            FROM evaluation_runs 
            WHERE evaluation_id = ANY($1::uuid[])
            AND status != 'cancelled'
            """,
            [stage_1_eval_id, stage_2_eval_id]
        )
        
        if not results or len(results) == 0:
            return None, f"No evaluation runs found for screener evaluations {stage_1_eval_id} and {stage_2_eval_id}"
            
        result = results[0]
        solved_count = result['solved_count'] or 0
        total_count = result['total_count'] or 0
        
        if total_count == 0:
            return None, f"No evaluation runs to calculate score from (total_count=0)"
            
        return solved_count / total_count, None

    @property
    def stage(self) -> Optional[int]:
        """Get the screening stage for this screener"""
        return self.get_stage(self.hotkey)
    
    def get_type(self) -> str:
        return "screener"
    
    def is_available(self) -> bool:
        return self.status == "available"
    
    def update_system_metrics(self, cpu_percent: Optional[float], ram_percent: Optional[float], 
                            disk_percent: Optional[float], containers: Optional[int],
                            ram_total_gb: Optional[float] = None, disk_total_gb: Optional[float] = None) -> None:
        """Update system metrics for this screener"""
        self.cpu_percent = cpu_percent
        self.ram_percent = ram_percent
        self.ram_total_gb = ram_total_gb
        self.disk_percent = disk_percent
        self.disk_total_gb = disk_total_gb
        self.containers = containers
        logger.debug(f"Updated system metrics for screener {self.hotkey}: CPU={cpu_percent}%, RAM={ram_percent}% ({ram_total_gb}GB), Disk={disk_percent}% ({disk_total_gb}GB), Containers={containers}")
    
    def _broadcast_status_change(self) -> None:
        """Broadcast status change to dashboard clients"""
        try:
            import asyncio
            from api.src.socket.websocket_manager import WebSocketManager
            
            # Create a task to send the status update
            loop = asyncio.get_event_loop()
            loop.create_task(self._async_broadcast_status_change())
        except Exception as e:
            logger.warning(f"Failed to broadcast status change for screener {self.hotkey}: {e}")
    
    async def _async_broadcast_status_change(self) -> None:
        """Async method to broadcast status change"""
        try:
            from api.src.socket.websocket_manager import WebSocketManager
            ws_manager = WebSocketManager.get_instance()
            
            await ws_manager.send_to_all_non_validators("validator-status-changed", {
                "type": "screener",
                "screener_hotkey": self.hotkey,
                "status": self.status,
                "screening_id": self.screening_id,
                "screening_agent_hotkey": self.screening_agent_hotkey,
                "screening_agent_name": self.screening_agent_name
            })
        except Exception as e:
            logger.error(f"Failed to send status change broadcast for screener {self.hotkey}: {e}")
    
    def set_available(self) -> None:
        """Set screener to available state"""
        old_status = getattr(self, 'status', None)
        self.status = "available"
        self.current_evaluation_id = None
        self.current_agent_name = None
        self.current_agent_hotkey = None
        logger.info(f"Screener {self.hotkey}: {old_status} -> available")
        
        # Broadcast status change if status actually changed
        if old_status != self.status and old_status is not None:
            self._broadcast_status_change()

    # Property mappings for get_clients method
    @property
    def screening_id(self) -> Optional[str]:
        return self.current_evaluation_id
    
    @property
    def screening_agent_hotkey(self) -> Optional[str]:
        return self.current_agent_hotkey
    
    @property
    def screening_agent_name(self) -> Optional[str]:
        return self.current_agent_name

    async def start_screening(self, evaluation_id: str) -> bool:
        """Handle start-evaluation message"""
        from api.src.models.evaluation import Evaluation
        
        evaluation = await Evaluation.get_by_id(evaluation_id)
        if not evaluation or not evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
            return False
        
        async with get_transaction() as conn:
            agent = await conn.fetchrow("SELECT status, agent_name, miner_hotkey FROM miner_agents WHERE version_id = $1", evaluation.version_id)
            agent_status = AgentStatus.from_string(agent["status"]) if agent else None
            
            # Check if agent is in the appropriate screening status for this screener stage
            expected_status = getattr(AgentStatus, f"screening_{self.stage}")
            if not agent or agent_status != expected_status:
                logger.info(f"Stage {self.stage} screener {self.hotkey}: tried to start screening but agent is not in screening_{self.stage} status (current: {agent['status'] if agent else 'None'})")
                return False
            agent_name = agent["agent_name"]
            agent_hotkey = agent["miner_hotkey"]

            await evaluation.start(conn)
            old_status = self.status
            self.status = f"screening"
            self.current_evaluation_id = evaluation_id
            self.current_agent_name = agent_name
            self.current_agent_hotkey = agent_hotkey
            logger.info(f"Screener {self.hotkey}: {old_status} -> screening {agent_name}")
            
            # Broadcast status change
            self._broadcast_status_change()
            return True
    
    async def connect(self):
        """Handle screener connection"""
        from api.src.models.evaluation import Evaluation
        logger.info(f"Screener {self.hotkey} connected")
        async with Evaluation.get_lock():
            # Only set available if not currently screening
            if self.status != "screening":
                self.set_available()
                logger.info(f"Screener {self.hotkey} available with status: {self.status}")
                await Evaluation.screen_next_awaiting_agent(self)
            else:
                logger.info(f"Screener {self.hotkey} reconnected but still screening - not assigning new work")
    
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

        logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}, entered finish_screening")
        
        try:
            evaluation = await Evaluation.get_by_id(evaluation_id)
            if not evaluation or not evaluation.is_screening or evaluation.validator_hotkey != self.hotkey:
                logger.warning(f"Screener {self.hotkey}: Invalid finish_screening call for evaluation {evaluation_id}")
                return
            
            async with get_transaction() as conn:
                agent_status = await conn.fetchval("SELECT status FROM miner_agents WHERE version_id = $1", evaluation.version_id)
                expected_status = getattr(AgentStatus, f"screening_{self.stage}")
                if AgentStatus.from_string(agent_status) != expected_status:
                    logger.warning(f"Stage {self.stage} screener {self.hotkey}: Evaluation {evaluation_id}: Agent {evaluation.version_id} not in screening_{self.stage} status during finish (current: {agent_status})")
                    # Clearly a bug here, its somehow set to failed_screening_1 when we hit this if statement
                    # It should be screening_1, no idea whats setting it to failed_screening_1
                    # return
                
                if errored:
                    logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}")
                    await evaluation.error(conn, reason)
                    logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}: done")
                    notification_targets = None
                else:
                    notification_targets = await evaluation.finish(conn)

                from api.src.socket.websocket_manager import WebSocketManager
                ws_manager = WebSocketManager.get_instance()
                await ws_manager.send_to_all_non_validators("evaluation-finished", {"evaluation_id": evaluation_id})

                self.set_available()
                    
                logger.info(f"Screener {self.hotkey}: Successfully finished evaluation {evaluation_id}, errored={errored}")
            
            # Handle notifications AFTER transaction commits
            if notification_targets:
                # Notify stage 2 screener when stage 1 completes
                if notification_targets.get("stage2_screener"):
                    async with Evaluation.get_lock():
                        await Evaluation.screen_next_awaiting_agent(notification_targets["stage2_screener"])
                
                # Notify validators with proper lock protection  
                for validator in notification_targets.get("validators", []):
                    async with Evaluation.get_lock():
                        if validator.is_available():
                            success = await validator.start_evaluation_and_send(evaluation_id)
                            if success:
                                logger.info(f"Successfully assigned evaluation {evaluation_id} to validator {validator.hotkey}")
                            else:
                                logger.warning(f"Failed to assign evaluation {evaluation_id} to validator {validator.hotkey}")
                        else:
                            logger.info(f"Validator {validator.hotkey} not available for evaluation {evaluation_id}")
            
            logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}: Got to end of try block")
        finally:
            logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}, in finally block")
            # Single atomic reset and reassignment
            async with Evaluation.get_lock():
                self.set_available()
                logger.info(f"Screener {self.hotkey}: Reset to available and looking for next agent")
                await Evaluation.screen_next_awaiting_agent(self)
            logger.info(f"Screener {self.hotkey}: Finishing screening {evaluation_id}, exiting finally block")
    
    @staticmethod
    async def get_first_available() -> Optional['Screener']:
        """Read-only availability check - does NOT reserve screener"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        logger.debug(f"Checking {len(ws_manager.clients)} clients for available screener...")
        for client in ws_manager.clients.values():
            if client.get_type() == "screener" and client.status == "available":
                logger.debug(f"Found available screener: {client.hotkey}")
                return client
        logger.warning("No available screeners found")
        return None
    
    @staticmethod
    async def get_first_available_and_reserve(stage: int) -> Optional['Screener']:
        """Atomically find and reserve first available screener for specific stage - MUST be called within Evaluation lock"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        
        for client in ws_manager.clients.values():
            if (client.get_type() == "screener" and 
                client.status == "available" and
                client.is_available() and
                client.stage == stage):
                
                # Immediately reserve to prevent race conditions
                client.status = "reserving"
                logger.info(f"Reserved stage {stage} screener {client.hotkey} for work assignment")
                
                return client
        
        logger.warning(f"No available stage {stage} screeners to reserve")
        return None
    