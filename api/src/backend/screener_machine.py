import logging
from typing import Callable, Dict, Tuple, Optional, TYPE_CHECKING
from enum import Enum
import asyncpg

if TYPE_CHECKING:
    from api.src.backend.entities import Screener

logger = logging.getLogger(__name__)

class ScreenerState(Enum):
    available = "available"
    running_screening = "running_screening"

class ScreenerStateTransitionError(Exception):
    pass

class ScreenerStateMachine:
    """Manages screener state transitions"""
    
    def __init__(self):
        self.transitions: Dict[Tuple[ScreenerState, ScreenerState], Callable] = {
            (ScreenerState.available, ScreenerState.running_screening): self._start_screening,
            (ScreenerState.running_screening, ScreenerState.available): self._finish_screening,
        }
    
    async def transition(self, screener: 'Screener', from_state: ScreenerState, 
                        to_state: ScreenerState, **context):
        """Execute a screener state transition"""
        handler = self.transitions.get((from_state, to_state))
        
        if not handler:
            raise ScreenerStateTransitionError(f"Invalid screener transition: {from_state} -> {to_state}")
        
        # Execute transition-specific logic
        await handler(screener, to_state, **context)
        
        # Update screener status
        screener.status = to_state.value
        
        logger.info(f"Screener {screener.hotkey}: {from_state} -> {to_state}")
    
    async def _start_screening(self, screener: 'Screener', to_state: ScreenerState, 
                              evaluation_id: str = None, agent_name: str = None, **context):
        """Screener starts running a screening"""
        if evaluation_id and agent_name:
            screener.status = f"Screening agent {agent_name} with evaluation {evaluation_id}"
        else:
            screener.status = to_state.value
    
    async def _finish_screening(self, screener: 'Screener', to_state: ScreenerState, **context):
        """Screener finishes a screening"""
        screener.status = to_state.value
    
    def get_current_state(self, screener: 'Screener') -> ScreenerState:
        """Get current state from screener status"""
        if screener.status == "available":
            return ScreenerState.available
        else:
            return ScreenerState.running_screening
    
    async def set_available(self, screener: 'Screener'):
        """Set screener to available state"""
        current_state = self.get_current_state(screener)
        if current_state == ScreenerState.running_screening:
            await self.transition(screener, current_state, ScreenerState.available)
        else:
            screener.status = ScreenerState.available.value
    
    async def set_running_screening(self, screener: 'Screener', evaluation_id: str, agent_name: str):
        """Set screener to running screening state"""
        current_state = self.get_current_state(screener)
        await self.transition(screener, current_state, ScreenerState.running_screening,
                             evaluation_id=evaluation_id, agent_name=agent_name) 