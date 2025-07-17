"""
Robust State Machine for Evaluation System

This module provides airtight state management with clear transitions,
perfect disconnect handling, and comprehensive error recovery.

States are managed through the existing database schema with validation
layers that ensure consistency and prevent race conditions.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from contextlib import asynccontextmanager
import asyncpg
from dataclasses import dataclass
import uuid

from api.src.backend.db_manager import db_operation, get_db_connection
from api.src.backend.entities import EvaluationStatus, AgentStatus
from api.src.utils.config import SCREENING_THRESHOLD

logger = logging.getLogger(__name__)

@dataclass
class StateTransition:
    """Records a state transition with full context"""
    entity_type: str  # 'agent' or 'evaluation'
    entity_id: str
    from_state: str
    to_state: str
    timestamp: datetime
    reason: str
    metadata: Dict = None


class StateValidator:
    """Validates state transitions and prevents invalid states"""
    
    # Valid state transitions for miner agents
    AGENT_TRANSITIONS = {
        AgentStatus.awaiting_screening: {
            AgentStatus.screening,           # Screener picks it up
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.screening: {
            AgentStatus.failed_screening,    # Failed screening
            AgentStatus.waiting,  # Passed screening
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.failed_screening: {
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.waiting: {
            AgentStatus.evaluating,          # Validator picks it up
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.evaluating: {
            AgentStatus.scored,              # All evaluations complete
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.scored: {
            AgentStatus.replaced             # New version uploaded
        },
        AgentStatus.replaced: {
            # Terminal state - no transitions allowed
        }
    }
    
    # Valid state transitions for evaluations
    EVALUATION_TRANSITIONS = {
        EvaluationStatus.waiting: {
            EvaluationStatus.running,       # Validator starts evaluation
            EvaluationStatus.timedout,      # Timeout waiting for validator
            EvaluationStatus.replaced        # Agent replaced
        },
        EvaluationStatus.running: {
            EvaluationStatus.completed,      # Successfully finished
            EvaluationStatus.error,         # Error during evaluation
            EvaluationStatus.timedout,      # Timeout during evaluation
            EvaluationStatus.replaced        # Agent replaced
        },
        EvaluationStatus.completed: {
            EvaluationStatus.replaced        # Agent replaced
        },
        EvaluationStatus.error: {
            EvaluationStatus.replaced        # Agent replaced
        },
        EvaluationStatus.timedout: {
            EvaluationStatus.replaced        # Agent replaced
        },
        EvaluationStatus.replaced: {
            # Terminal state - no transitions allowed
        }
    }
    
    @classmethod
    def validate_agent_transition(cls, from_state: AgentStatus, to_state: AgentStatus) -> bool:
        """Check if agent state transition is valid"""
        allowed_states = cls.AGENT_TRANSITIONS.get(from_state, set())
        return to_state in allowed_states
    
    @classmethod
    def validate_evaluation_transition(cls, from_state: EvaluationStatus, to_state: EvaluationStatus) -> bool:
        """Check if evaluation state transition is valid"""
        allowed_states = cls.EVALUATION_TRANSITIONS.get(from_state, set())
        return to_state in allowed_states


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class ConcurrencyError(Exception):
    """Raised when concurrent operations conflict"""
    pass


class EvaluationStateMachine:
    """
    The heart of the evaluation system - manages all state transitions
    with atomic operations and perfect error handling.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized') or not self._initialized:
            self.validator = StateValidator()
            self._transition_log: List[StateTransition] = []
            self._initialized = True
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance of the state machine"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _get_websocket_manager(self):
        """Get WebSocketManager instance - imported here to avoid circular import"""
        from api.src.socket.websocket_manager import WebSocketManager
        return WebSocketManager.get_instance()
    
    @asynccontextmanager
    async def atomic_transaction(self):
        """
        Ensures all state changes happen atomically or not at all.
        This prevents the system from ever being in an inconsistent state.
        """
        async with get_db_connection() as conn:
            async with conn.transaction():
                yield conn
    
    async def log_transition(self, transition: StateTransition):
        """Log state transition for debugging and auditing"""
        self._transition_log.append(transition)
        logger.info(f"State transition: {transition.entity_type} {transition.entity_id} "
                   f"{transition.from_state} -> {transition.to_state} ({transition.reason})")
    
    # ===== AGENT UPLOAD AND REPLACEMENT =====
    
    async def upload_new_agent(self, miner_hotkey: str, agent_name: str, version_num: int) -> Tuple[str, bool]:
        """
        Upload a new agent version. This is the entry point for all new agents.
        Returns (version_id, success)
        """
        async with self.atomic_transaction() as conn:
            # Check if there's already a running evaluation for this miner
            has_running = await self._has_running_evaluation_for_miner(conn, miner_hotkey)
            if has_running:
                logger.warning(f"Cannot upload agent for {miner_hotkey} - evaluation already running")
                return None, False
            
            # Replace all older versions first
            await self._replace_older_versions(conn, miner_hotkey)
            
            # Check if screener is available
            screener_hotkey = await self._get_available_screener()
            if not screener_hotkey:
                logger.warning(f"No screener available for {miner_hotkey}")
                return None, False
            
            # Create new agent
            version_id = str(uuid.uuid4())
            await conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES ($1, $2, $3, $4, NOW(), $5)
            """, version_id, miner_hotkey, agent_name, version_num, AgentStatus.awaiting_screening.value)
            
            # Create screening evaluation
            await self._create_screening_evaluation(conn, version_id, screener_hotkey)
            
            # Notify screener
            ws_manager = self._get_websocket_manager()
            await ws_manager.send_to_validator(screener_hotkey, {
                "type": "screen-agent",
                "version_id": version_id
            })
            
            await self.log_transition(StateTransition(
                entity_type="agent",
                entity_id=version_id,
                from_state="none",
                to_state=AgentStatus.awaiting_screening.value,
                timestamp=datetime.now(timezone.utc),
                reason="agent_uploaded",
                metadata={"miner_hotkey": miner_hotkey, "screener": screener_hotkey}
            ))
            
            return version_id, True
    
    async def _replace_older_versions(self, conn: asyncpg.Connection, miner_hotkey: str):
        """Replace all older versions of an agent when a new version is uploaded"""
        # Get all older versions for this miner
        older_versions = await conn.fetch("""
            SELECT version_id, status FROM miner_agents 
            WHERE miner_hotkey = $1 AND status != 'replaced'
        """, miner_hotkey)
        
        for version_row in older_versions:
            version_id = version_row["version_id"]
            current_status = version_row["status"]
            
            # Map current status to enum
            current_state = AgentStatus.from_string(current_status)
            
            # Skip if already replaced
            if current_state == AgentStatus.replaced:
                continue
            
            # Validate transition
            if not self.validator.validate_agent_transition(current_state, AgentStatus.replaced):
                logger.warning(f"Cannot replace agent {version_id} in state {current_state}")
                continue
            
            # Replace all evaluations for this agent
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW() 
                WHERE version_id = $2 AND status IN ($3, $4)
            """, EvaluationStatus.replaced.value, version_id, EvaluationStatus.waiting.value, EvaluationStatus.running.value)
            
            # Update agent state
            await conn.execute("""
                UPDATE miner_agents 
                SET status = $1 
                WHERE version_id = $2
            """, AgentStatus.replaced.value, version_id)
            
            await self.log_transition(StateTransition(
                entity_type="agent",
                entity_id=version_id,
                from_state=current_state.value,
                to_state=AgentStatus.replaced.value,
                timestamp=datetime.now(timezone.utc),
                reason="newer_version_uploaded",
                metadata={"miner_hotkey": miner_hotkey}
            ))
    
    # ===== SCREENING FLOW =====
    
    async def start_screening(self, evaluation_id: str, screener_hotkey: str) -> bool:
        """
        Start screening evaluation. This is called when screener picks up the evaluation.
        """
        async with self.atomic_transaction() as conn:
            # Get evaluation info
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, ma.status as agent_status
                FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1 AND e.validator_hotkey = $2
            """, evaluation_id, screener_hotkey)
            
            if not eval_info:
                logger.warning(f"Evaluation {evaluation_id} not found for screener {screener_hotkey}")
                return False
            
            # Check if evaluation is in correct state
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.waiting:
                logger.warning(f"Evaluation {evaluation_id} not in QUEUED state: {eval_state}")
                return False
            
            # Check if agent is in correct state
            agent_state = AgentStatus.from_string(eval_info["agent_status"])
            if agent_state != AgentStatus.awaiting_screening:
                logger.warning(f"Agent {eval_info['version_id']} not in PENDING_SCREENING state: {agent_state}")
                return False
            
            # Update evaluation state
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, started_at = NOW() 
                WHERE evaluation_id = $2
            """, EvaluationStatus.running.value, evaluation_id)
            
            # Update agent state
            await conn.execute("""
                UPDATE miner_agents 
                SET status = $1 
                WHERE version_id = $2
            """, AgentStatus.screening.value, eval_info["version_id"])
            
            await self.log_transition(StateTransition(
                entity_type="evaluation",
                entity_id=evaluation_id,
                from_state=EvaluationStatus.waiting.value,
                to_state=EvaluationStatus.running.value,
                timestamp=datetime.now(timezone.utc),
                reason="screener_started",
                metadata={"screener": screener_hotkey}
            ))
            
            return True
    
    async def finish_screening(self, evaluation_id: str, score: float) -> bool:
        """
        Finish screening evaluation. This determines if agent passes or fails.
        """
        async with self.atomic_transaction() as conn:
            # Get evaluation info
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, e.validator_hotkey
                FROM evaluations e
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_info:
                logger.warning(f"Evaluation {evaluation_id} not found")
                return False
            
            # Check if evaluation is in correct state
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.running:
                logger.warning(f"Evaluation {evaluation_id} not in ASSIGNED state: {eval_state}")
                return False
            
            # Update evaluation state
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW(), score = $2
                WHERE evaluation_id = $3
            """, EvaluationStatus.completed.value, score, evaluation_id)
            
            # Determine agent's next state based on score
            if score >= SCREENING_THRESHOLD:
                # Screening passed - create evaluations for validators
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1, score = $2
                    WHERE version_id = $3
                """, AgentStatus.waiting.value, score, eval_info["version_id"])
                
                # Create evaluations for all connected validators
                await self._create_validator_evaluations(conn, eval_info["version_id"])
                
                # Notify validators
                ws_manager = self._get_websocket_manager()
                await ws_manager.broadcast_to_validators({
                    "type": "evaluation-available",
                    "version_id": eval_info["version_id"]
                })
                
                next_state = AgentStatus.waiting
                reason = "screening_passed"
            else:
                # Screening failed
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1, score = $2
                    WHERE version_id = $3
                """, AgentStatus.failed_screening.value, score, eval_info["version_id"])
                
                next_state = AgentStatus.failed_screening
                reason = "screening_failed"
            
            await self.log_transition(StateTransition(
                entity_type="evaluation",
                entity_id=evaluation_id,
                from_state=EvaluationStatus.running.value,
                to_state=EvaluationStatus.completed.value,
                timestamp=datetime.now(timezone.utc),
                reason="screening_completed",
                metadata={"score": score}
            ))
            
            await self.log_transition(StateTransition(
                entity_type="agent",
                entity_id=eval_info["version_id"],
                from_state=AgentStatus.screening.value,
                to_state=next_state.value,
                timestamp=datetime.now(timezone.utc),
                reason=reason,
                metadata={"score": score}
            ))
            
            return True
    
    # ===== EVALUATION FLOW =====
    
    async def start_evaluation(self, evaluation_id: str, validator_hotkey: str) -> bool:
        """
        Start regular evaluation. This is called when validator picks up the evaluation.
        """
        async with self.atomic_transaction() as conn:
            # Get evaluation info
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, ma.status as agent_status
                FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1 AND e.validator_hotkey = $2
            """, evaluation_id, validator_hotkey)
            
            if not eval_info:
                logger.warning(f"Evaluation {evaluation_id} not found for validator {validator_hotkey}")
                return False
            
            # Check if evaluation is in correct state
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.waiting:
                logger.warning(f"Evaluation {evaluation_id} not in QUEUED state: {eval_state}")
                return False
            
            # Update evaluation state
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, started_at = NOW() 
                WHERE evaluation_id = $2
            """, EvaluationStatus.running.value, evaluation_id)
            
            # Update agent state to evaluating if it's the first evaluation to start
            agent_state = AgentStatus.from_string(eval_info["agent_status"])
            if agent_state == AgentStatus.waiting:
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1 
                    WHERE version_id = $2
                """, AgentStatus.evaluating.value, eval_info["version_id"])
            
            await self.log_transition(StateTransition(
                entity_type="evaluation",
                entity_id=evaluation_id,
                from_state=EvaluationStatus.waiting.value,
                to_state=EvaluationStatus.running.value,
                timestamp=datetime.now(timezone.utc),
                reason="validator_started",
                metadata={"validator": validator_hotkey}
            ))
            
            return True
    
    async def finish_evaluation(self, evaluation_id: str, score: float, errored: bool = False) -> bool:
        """
        Finish regular evaluation. This may trigger agent transition to scored.
        """
        async with self.atomic_transaction() as conn:
            # Get evaluation info
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, e.validator_hotkey
                FROM evaluations e
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_info:
                logger.warning(f"Evaluation {evaluation_id} not found")
                return False
            
            # Check if evaluation is in correct state
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.running:
                logger.warning(f"Evaluation {evaluation_id} not in ASSIGNED state: {eval_state}")
                return False
            
            # Update evaluation state
            final_status = "error" if errored else "completed"
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW(), score = $2
                WHERE evaluation_id = $3
            """, final_status, score, evaluation_id)
            
            # Check if all evaluations are complete for this agent
            still_running = await conn.fetchrow("""
                SELECT COUNT(*) as count FROM evaluations 
                WHERE version_id = $1 AND status IN ($2, $3)
            """, eval_info["version_id"], EvaluationStatus.waiting.value, EvaluationStatus.running.value)
            
            if still_running["count"] == 0:
                # All evaluations complete - mark agent as scored
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1 
                    WHERE version_id = $2
                """, AgentStatus.scored.value, eval_info["version_id"])
                
                await self.log_transition(StateTransition(
                    entity_type="agent",
                    entity_id=eval_info["version_id"],
                    from_state=AgentStatus.evaluating.value,
                    to_state=AgentStatus.scored.value,
                    timestamp=datetime.now(timezone.utc),
                    reason="all_evaluations_complete"
                ))
            
            final_eval_state = EvaluationStatus.error if errored else EvaluationStatus.completed
            await self.log_transition(StateTransition(
                entity_type="evaluation",
                entity_id=evaluation_id,
                from_state=EvaluationStatus.running.value,
                to_state=final_eval_state.value,
                timestamp=datetime.now(timezone.utc),
                reason="evaluation_finished",
                metadata={"score": score, "errored": errored}
            ))
            
            return True
    
    # ===== DISCONNECT HANDLING =====
    
    async def handle_screener_disconnect(self, screener_hotkey: str):
        """
        Handle screener disconnect. Reset any running screening evaluations.
        """
        async with self.atomic_transaction() as conn:
            # Find all running screening evaluations for this screener
            running_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id
                FROM evaluations e
                WHERE e.validator_hotkey = $1 
                AND e.status = $2
                AND e.validator_hotkey LIKE 'i-0%'
            """, screener_hotkey, EvaluationStatus.running.value)
            
            for eval_row in running_evals:
                evaluation_id = eval_row["evaluation_id"]
                version_id = eval_row["version_id"]
                
                # Reset evaluation to waiting
                await conn.execute("""
                    UPDATE evaluations 
                    SET status = $1, started_at = NULL
                    WHERE evaluation_id = $2
                """, EvaluationStatus.waiting.value, evaluation_id)
                
                # Reset agent to pending screening
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1
                    WHERE version_id = $2
                """, AgentStatus.awaiting_screening.value, version_id)
                
                await self.log_transition(StateTransition(
                    entity_type="evaluation",
                    entity_id=evaluation_id,
                    from_state=EvaluationStatus.running.value,
                    to_state=EvaluationStatus.waiting.value,
                    timestamp=datetime.now(timezone.utc),
                    reason="screener_disconnected",
                    metadata={"screener": screener_hotkey}
                ))
                
                # Try to reassign to another screener
                new_screener = await self._get_available_screener()
                if new_screener:
                    ws_manager = self._get_websocket_manager()
                    await ws_manager.send_to_validator(new_screener, {
                        "type": "screen-agent",
                        "evaluation_id": evaluation_id,
                        "version_id": version_id
                    })
    
    async def handle_validator_disconnect(self, validator_hotkey: str):
        """
        Handle validator disconnect. Reset any running evaluations.
        """
        async with self.atomic_transaction() as conn:
            # Find all running evaluations for this validator
            running_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id
                FROM evaluations e
                WHERE e.validator_hotkey = $1 
                AND e.status = $2
                AND e.validator_hotkey NOT LIKE 'i-0%'
            """, validator_hotkey, EvaluationStatus.running.value)
            
            for eval_row in running_evals:
                evaluation_id = eval_row["evaluation_id"]
                version_id = eval_row["version_id"]
                
                # Reset evaluation to waiting
                await conn.execute("""
                    UPDATE evaluations 
                    SET status = $1, started_at = NULL
                    WHERE evaluation_id = $2
                """, EvaluationStatus.waiting.value, evaluation_id)
                
                # Check if agent should go back to waiting state
                still_running = await conn.fetchrow("""
                    SELECT COUNT(*) as count FROM evaluations 
                    WHERE version_id = $1 AND status = $2
                """, version_id, EvaluationStatus.running.value)
                
                if still_running["count"] == 0:
                    # No more running evaluations - back to waiting
                    await conn.execute("""
                        UPDATE miner_agents 
                        SET status = $1
                        WHERE version_id = $2
                    """, AgentStatus.waiting.value, version_id)
                
                await self.log_transition(StateTransition(
                    entity_type="evaluation",
                    entity_id=evaluation_id,
                    from_state=EvaluationStatus.running.value,
                    to_state=EvaluationStatus.waiting.value,
                    timestamp=datetime.now(timezone.utc),
                    reason="validator_disconnected",
                    metadata={"validator": validator_hotkey}
                ))
                
                # Notify other validators about available evaluation
                ws_manager = self._get_websocket_manager()
                await ws_manager.broadcast_to_validators({
                    "type": "evaluation-available",
                    "evaluation_id": evaluation_id,
                    "version_id": version_id
                })
    
    # ===== TIMEOUT HANDLING =====
    
    async def handle_timeouts(self):
        """
        Handle evaluation timeouts. This should be called periodically.
        """
        async with self.atomic_transaction() as conn:
            now = datetime.now(timezone.utc)
            
            # Timeout screening evaluations (10 minutes)
            screening_timeout = now - timedelta(minutes=10)
            screening_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey
                FROM evaluations e
                WHERE e.status = $1 
                AND e.validator_hotkey LIKE 'i-0%'
                AND e.started_at < $2
            """, EvaluationStatus.running.value, screening_timeout)
            
            for eval_row in screening_evals:
                await self._timeout_evaluation(conn, eval_row["evaluation_id"], eval_row["version_id"], "screening_timeout")
            
            # Timeout regular evaluations (60 minutes)
            eval_timeout = now - timedelta(minutes=60)
            regular_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey
                FROM evaluations e
                WHERE e.status = $1 
                AND e.validator_hotkey NOT LIKE 'i-0%'
                AND e.started_at < $2
            """, EvaluationStatus.running.value, eval_timeout)
            
            for eval_row in regular_evals:
                await self._timeout_evaluation(conn, eval_row["evaluation_id"], eval_row["version_id"], "evaluation_timeout")
    
    async def _timeout_evaluation(self, conn: asyncpg.Connection, evaluation_id: str, version_id: str, reason: str):
        """Timeout a specific evaluation"""
        # Update evaluation state
        await conn.execute("""
            UPDATE evaluations 
            SET status = $1, finished_at = NOW()
            WHERE evaluation_id = $2
        """, EvaluationStatus.timedout.value, evaluation_id)
        
        # Check if all evaluations are complete for this agent
        still_running = await conn.fetchrow("""
            SELECT COUNT(*) as count FROM evaluations 
            WHERE version_id = $1 AND status IN ($2, $3)
        """, version_id, EvaluationStatus.waiting.value, EvaluationStatus.running.value)
        
        if still_running["count"] == 0:
            # All evaluations complete - mark agent as scored
            await conn.execute("""
                UPDATE miner_agents 
                SET status = $1 
                WHERE version_id = $2
            """, AgentStatus.scored.value, version_id)
        
        await self.log_transition(StateTransition(
            entity_type="evaluation",
            entity_id=evaluation_id,
            from_state=EvaluationStatus.running.value,
            to_state=EvaluationStatus.timedout.value,
            timestamp=datetime.now(timezone.utc),
            reason=reason
        ))
    
    # ===== HELPER METHODS =====
    
    async def _has_running_evaluation_for_miner(self, conn: asyncpg.Connection, miner_hotkey: str) -> bool:
        """Check if miner has any running evaluations"""
        result = await conn.fetchrow("""
            SELECT COUNT(*) as count FROM evaluations e
            JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE ma.miner_hotkey = $1 AND e.status = $2
        """, miner_hotkey, EvaluationStatus.running.value)
        return result["count"] > 0
    
    async def _get_available_screener(self) -> Optional[str]:
        """Get an available screener hotkey"""
        # Get all connected screeners
        ws_manager = self._get_websocket_manager()
        screeners = await ws_manager.get_connected_screeners()
        
        # Find one that's not currently screening
        for screener in screeners:
            if not await self._screener_is_busy(screener):
                return screener
        
        return None
    
    async def _screener_is_busy(self, screener_hotkey: str) -> bool:
        """Check if screener is currently busy"""
        async with get_db_connection() as conn:
            result = await conn.fetchrow("""
                SELECT COUNT(*) as count FROM evaluations 
                WHERE validator_hotkey = $1 AND status = $2
            """, screener_hotkey, EvaluationStatus.running.value)
            return result["count"] > 0
    
    async def _create_screening_evaluation(self, conn: asyncpg.Connection, version_id: str, screener_hotkey: str):
        """Create screening evaluation for agent"""
        evaluation_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
            VALUES ($1, $2, $3, $4, NOW())
        """, evaluation_id, version_id, screener_hotkey, EvaluationStatus.waiting.value)
        return evaluation_id
    
    async def _create_validator_evaluations(self, conn: asyncpg.Connection, version_id: str):
        """Create evaluations for all connected validators"""
        # Get all connected validators (non-screeners)
        ws_manager = self._get_websocket_manager()
        validators = await ws_manager.get_connected_validators()
        
        for validator_hotkey in validators:
            # Skip screeners
            if validator_hotkey.startswith('i-0'):
                continue
            
            # Check if evaluation already exists
            existing = await conn.fetchrow("""
                SELECT evaluation_id FROM evaluations 
                WHERE version_id = $1 AND validator_hotkey = $2
            """, version_id, validator_hotkey)
            
            if existing:
                continue
            
            # Create evaluation
            evaluation_id = str(uuid.uuid4())
            await conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
                VALUES ($1, $2, $3, $4, NOW())
            """, evaluation_id, version_id, validator_hotkey, EvaluationStatus.waiting.value)
    

    
    async def get_next_screening_evaluation(self, screener_hotkey: str) -> Optional[object]:
        """Get the next screening evaluation for a screener"""
        from api.src.backend.queries.evaluations import get_next_evaluation_for_screener
        return await get_next_evaluation_for_screener()


# Global state machine instance - singleton
state_machine = EvaluationStateMachine.get_instance()

# Alternative function for getting the singleton instance
def get_state_machine() -> EvaluationStateMachine:
    """Get the singleton state machine instance"""
    return EvaluationStateMachine.get_instance()