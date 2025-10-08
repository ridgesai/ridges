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
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")

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
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    async def _async_broadcast_status_change(self) -> None:
        """Async method to broadcast status change"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
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
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
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
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    @staticmethod
    async def get_first_available() -> Optional['Screener']:
        """Read-only availability check - does NOT reserve screener"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    
    @staticmethod
    async def get_first_available_and_reserve(stage: int) -> Optional['Screener']:
        """Atomically find and reserve first available screener for specific stage - MUST be called within Evaluation lock"""
        raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")
    