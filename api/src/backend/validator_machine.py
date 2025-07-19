import logging
from typing import Callable, Dict, Tuple, Optional, TYPE_CHECKING
from enum import Enum
import asyncpg

if TYPE_CHECKING:
    from api.src.backend.entities import Validator

logger = logging.getLogger(__name__)

class ValidatorState(Enum):
    available = "available"
    running_evaluation = "running_evaluation"

class ValidatorStateTransitionError(Exception):
    pass

class ValidatorStateMachine:
    """Manages validator state transitions"""
    
    def __init__(self):
        self.transitions: Dict[Tuple[ValidatorState, ValidatorState], Callable] = {
            (ValidatorState.available, ValidatorState.running_evaluation): self._start_evaluation,
            (ValidatorState.running_evaluation, ValidatorState.available): self._finish_evaluation,
        }
    
    async def transition(self, validator: 'Validator', from_state: ValidatorState, 
                        to_state: ValidatorState, **context):
        """Execute a validator state transition"""
        handler = self.transitions.get((from_state, to_state))
        
        if not handler:
            raise ValidatorStateTransitionError(f"Invalid validator transition: {from_state} -> {to_state}")
        
        # Execute transition-specific logic
        await handler(validator, to_state, **context)
        
        # Update validator status
        validator.status = to_state.value
        
        logger.info(f"Validator {validator.hotkey}: {from_state} -> {to_state}")
    
    async def _start_evaluation(self, validator: 'Validator', to_state: ValidatorState, 
                               evaluation_id: str = None, agent_name: str = None, **context):
        """Validator starts running an evaluation"""
        if evaluation_id and agent_name:
            validator.status = f"Evaluating agent {agent_name} with evaluation {evaluation_id}"
        else:
            validator.status = to_state.value
    
    async def _finish_evaluation(self, validator: 'Validator', to_state: ValidatorState, **context):
        """Validator finishes an evaluation"""
        validator.status = to_state.value
    
    def get_current_state(self, validator: 'Validator') -> ValidatorState:
        """Get current state from validator status"""
        if validator.status == "available":
            return ValidatorState.available
        else:
            return ValidatorState.running_evaluation
    
    async def set_available(self, validator: 'Validator'):
        """Set validator to available state"""
        current_state = self.get_current_state(validator)
        if current_state == ValidatorState.running_evaluation:
            await self.transition(validator, current_state, ValidatorState.available)
        else:
            validator.status = ValidatorState.available.value
    
    async def set_running_evaluation(self, validator: 'Validator', evaluation_id: str, agent_name: str):
        """Set validator to running evaluation state"""
        current_state = self.get_current_state(validator)
        await self.transition(validator, current_state, ValidatorState.running_evaluation,
                             evaluation_id=evaluation_id, agent_name=agent_name) 