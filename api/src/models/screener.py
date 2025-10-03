import logging
from typing import Literal, Optional, List
import asyncpg

from api.src.backend.entities import Client

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
    