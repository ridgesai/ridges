import logging
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING
import asyncpg
import uuid

from api.src.backend.entities import EvaluationStatus

if TYPE_CHECKING:
    from api.src.socket.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

class EvaluationStateTransitionError(Exception):
    pass

class EvaluationStateMachine:
    """Pure evaluation logic - transitions, creation, validation"""
    
    def __init__(self):
        self.transitions: Dict[Tuple[EvaluationStatus, EvaluationStatus], Callable] = {
            (EvaluationStatus.waiting, EvaluationStatus.running): self._start,
            (EvaluationStatus.waiting, EvaluationStatus.error): self._error,
            (EvaluationStatus.waiting, EvaluationStatus.replaced): self._replace,
            (EvaluationStatus.running, EvaluationStatus.completed): self._complete,
            (EvaluationStatus.running, EvaluationStatus.error): self._error,
            (EvaluationStatus.running, EvaluationStatus.replaced): self._replace,
            (EvaluationStatus.running, EvaluationStatus.waiting): self._reset_to_waiting,
        }
    
    async def transition(self, conn: asyncpg.Connection, evaluation_id: str,
                        from_state: EvaluationStatus, to_state: EvaluationStatus, **context):
        """Execute an evaluation state transition"""
        handler = self.transitions.get((from_state, to_state))
        
        if not handler:
            raise EvaluationStateTransitionError(f"Invalid evaluation transition: {from_state} -> {to_state}")
        
        # Execute transition-specific logic
        await handler(conn, evaluation_id, **context)
        
        # Update database
        update_query = """
            UPDATE evaluations SET status = $1, finished_at = CASE 
                WHEN $1 IN ('completed', 'error', 'replaced') THEN NOW() 
                ELSE finished_at END
            WHERE evaluation_id = $2
        """
        await conn.execute(update_query, to_state.value, evaluation_id)
        
        logger.info(f"Evaluation {evaluation_id}: {from_state} -> {to_state}")
    
    # Transition handlers
    async def _start(self, conn: asyncpg.Connection, evaluation_id: str, **context):
        """Evaluation starts running"""
        await conn.execute("""
            UPDATE evaluations SET started_at = NOW() WHERE evaluation_id = $1
        """, evaluation_id)
    
    async def _complete(self, conn: asyncpg.Connection, evaluation_id: str, **context):
        """Evaluation completes successfully"""
        await conn.execute("""
            UPDATE evaluations SET finished_at = NOW() WHERE evaluation_id = $1
        """, evaluation_id)
    
    async def _error(self, conn: asyncpg.Connection, evaluation_id: str, 
                    reason: str = "unknown", **context):
        """Evaluation encounters an error"""
        await conn.execute("""
            UPDATE evaluations SET terminated_reason = $1 WHERE evaluation_id = $2
        """, reason, evaluation_id)
        # Cancel associated evaluation runs
        await conn.execute("""
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW()
            WHERE evaluation_id = $1 AND status != 'cancelled'
        """, evaluation_id)
    
    async def _replace(self, conn: asyncpg.Connection, evaluation_id: str, **context):
        """Evaluation is replaced (agent was replaced)"""
        # Cancel associated evaluation runs
        await conn.execute("""
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW()
            WHERE evaluation_id = $1 AND status != 'cancelled'
        """, evaluation_id)

    async def _reset_to_waiting(self, conn: asyncpg.Connection, evaluation_id: str, **context):
        """Reset evaluation back to waiting (e.g., due to validator disconnect)"""
        await conn.execute("""
            UPDATE evaluations SET started_at = NULL WHERE evaluation_id = $1
        """, evaluation_id)
        # Cancel associated evaluation runs since we're resetting
        await conn.execute("""
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW()
            WHERE evaluation_id = $1 AND status != 'cancelled'
        """, evaluation_id)
    
    
    async def has_active(self, conn: asyncpg.Connection, version_id: str, include_waiting: bool = True) -> bool:
        """Check if agent has active evaluations"""
        if include_waiting:
            statuses = [EvaluationStatus.waiting.value, EvaluationStatus.running.value]
            result = await conn.fetchrow("SELECT COUNT(*) as count FROM evaluations WHERE version_id = $1 AND status = ANY($2)", version_id, statuses)
        else:
            result = await conn.fetchrow("SELECT COUNT(*) as count FROM evaluations WHERE version_id = $1 AND status = $2", version_id, EvaluationStatus.running.value)
        return result["count"] > 0

    async def create_evaluation_for_validator(self, conn: asyncpg.Connection, version_id: str, validator_hotkey: str) -> str:
        """Create evaluation for a specific validator if one doesn't exist"""
        # Check if evaluation already exists
        existing = await conn.fetchrow("SELECT evaluation_id FROM evaluations WHERE version_id = $1 AND validator_hotkey = $2", version_id, validator_hotkey)
        if existing:
            return existing["evaluation_id"]
        
        from api.src.backend.queries.evaluation_sets import get_latest_set_id
        
        eval_id = str(uuid.uuid4())
        set_id = await get_latest_set_id()
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
        """, eval_id, version_id, validator_hotkey, set_id, EvaluationStatus.waiting.value)
        
        return eval_id

    async def create_screening(self, conn: asyncpg.Connection, version_id: str, screener_hotkey: str) -> str:
        """Create screening evaluation"""
        from api.src.backend.queries.evaluation_sets import get_latest_set_id
        
        eval_id = str(uuid.uuid4())
        set_id = await get_latest_set_id()
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
        """, eval_id, version_id, screener_hotkey, set_id, EvaluationStatus.waiting.value)
        
        return eval_id


    async def replace_for_agent(self, conn: asyncpg.Connection, version_id: str):
        """Replace all evaluations for agent"""
        # Get all active evaluations for this agent
        active_evals = await conn.fetch("""
            SELECT evaluation_id, status FROM evaluations 
            WHERE version_id = $1 AND status IN ($2, $3)
        """, version_id, EvaluationStatus.waiting.value, EvaluationStatus.running.value)
        
        # Use proper transitions for each evaluation
        for eval_row in active_evals:
            current_status = EvaluationStatus.from_string(eval_row["status"])
            await self.transition(conn, eval_row["evaluation_id"], current_status, EvaluationStatus.replaced)

    async def should_agent_be_waiting(self, conn: asyncpg.Connection, version_id: str) -> bool:
        """Check if agent should transition from evaluating to waiting"""
        # Agent should be waiting if it has waiting evaluations but no running ones
        return (await self.has_active(conn, version_id, include_waiting=True) and 
                not await self.has_active(conn, version_id, include_waiting=False))

    async def should_agent_be_scored(self, conn: asyncpg.Connection, version_id: str) -> bool:
        """Check if agent should transition from evaluating to scored"""
        # Agent should be scored if it has no active evaluations at all
        return not await self.has_active(conn, version_id, include_waiting=True)
    
    async def finish(self, conn: asyncpg.Connection, evaluation_id: str) -> bool:
        """Finish evaluation - returns True if successful"""
        try:
            await self.transition(conn, evaluation_id, EvaluationStatus.running, EvaluationStatus.completed)
            return True
        except EvaluationStateTransitionError:
            return False
    
    async def error_with_reason(self, conn: asyncpg.Connection, evaluation_id: str, reason: Optional[str]) -> bool:
        """Error evaluation with reason - returns True if successful"""
        try:
            await self.transition(conn, evaluation_id, EvaluationStatus.running, EvaluationStatus.error, reason=reason)
            return True
        except EvaluationStateTransitionError:
            return False
    
    async def start_evaluation(self, conn: asyncpg.Connection, evaluation_id: str, validator_hotkey: str) -> bool:
        """Start evaluation - returns True if successful"""
        # Validate evaluation can start
        info = await conn.fetchrow("""
            SELECT e.evaluation_id, e.version_id, e.status, ma.status as agent_status
            FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE e.evaluation_id = $1 AND e.validator_hotkey = $2
        """, evaluation_id, validator_hotkey)
        
        if not info:
            return False
        
        eval_state = EvaluationStatus.from_string(info["status"])
        if eval_state != EvaluationStatus.waiting:
            return False
        
        try:
            await self.transition(conn, evaluation_id, EvaluationStatus.waiting, EvaluationStatus.running)
            return True
        except EvaluationStateTransitionError:
            return False
    
    async def get_running_evaluations_for_validator(self, conn: asyncpg.Connection, validator_hotkey: str, is_screener: bool = False) -> list:
        """Get running evaluations for a validator/screener"""
        filter_clause = "AND e.validator_hotkey LIKE 'i-0%'" if is_screener else "AND e.validator_hotkey NOT LIKE 'i-0%'"
        return await conn.fetch(f"""
            SELECT e.evaluation_id, e.version_id
            FROM evaluations e
            WHERE e.validator_hotkey = $1 AND e.status = 'running' {filter_clause}
        """, validator_hotkey) 