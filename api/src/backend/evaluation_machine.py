import logging
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING, List
from contextlib import asynccontextmanager
import asyncpg
import uuid

from api.src.backend.entities import EvaluationStatus, AgentStatus
from api.src.backend.validator_machine import ValidatorStateMachine
from api.src.backend.screener_machine import ScreenerStateMachine
from api.src.backend.db_manager import get_db_connection
from api.src.utils.config import SCREENING_THRESHOLD

if TYPE_CHECKING:
    from api.src.socket.websocket_manager import WebSocketManager
    from api.src.backend.entities import Validator, Screener, MinerAgent

logger = logging.getLogger(__name__)

class EvaluationStateTransitionError(Exception):
    pass

class EvaluationStateMachine:
    """Core evaluation controller - manages evaluations and derives agent status from evaluation events"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized') or not self._initialized:
            from api.src.socket.websocket_manager import WebSocketManager
            self.ws_manager = WebSocketManager.get_instance()
            self.validator_machine = ValidatorStateMachine()
            self.screener_machine = ScreenerStateMachine()
            self.transitions: Dict[Tuple[EvaluationStatus, EvaluationStatus], Callable] = {
                (EvaluationStatus.waiting, EvaluationStatus.running): self._start,
                (EvaluationStatus.waiting, EvaluationStatus.error): self._error,
                (EvaluationStatus.waiting, EvaluationStatus.replaced): self._replace,
                (EvaluationStatus.running, EvaluationStatus.completed): self._complete,
                (EvaluationStatus.running, EvaluationStatus.error): self._error,
                (EvaluationStatus.running, EvaluationStatus.replaced): self._replace,
                (EvaluationStatus.running, EvaluationStatus.waiting): self._reset_to_waiting,
            }
            self._initialized = True
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @asynccontextmanager
    async def atomic_transaction(self):
        async with get_db_connection() as conn:
            async with conn.transaction():
                yield conn
    
    async def transition(self, conn: asyncpg.Connection, evaluation_id: str,
                        from_state: EvaluationStatus, to_state: EvaluationStatus, **context):
        """Execute an evaluation state transition and update agent status accordingly"""
        handler = self.transitions.get((from_state, to_state))
        
        if not handler:
            raise EvaluationStateTransitionError(f"Invalid evaluation transition: {from_state} -> {to_state}")
        
        # Execute transition-specific logic
        await handler(conn, evaluation_id, to_state, **context)
        
        # Update agent status based on evaluation events
        await self._update_agent_status_for_evaluation(conn, evaluation_id)
        
        logger.info(f"Evaluation {evaluation_id}: {from_state} -> {to_state}")
    
    async def _update_agent_status_for_evaluation(self, conn: asyncpg.Connection, evaluation_id: str):
        """Update agent status based on evaluation state"""
        # Get evaluation and agent info
        eval_data = await conn.fetchrow("""
            SELECT e.version_id, e.status, e.validator_hotkey, e.score
            FROM evaluations e 
            WHERE e.evaluation_id = $1
        """, evaluation_id)
        
        if not eval_data:
            return
        
        version_id = eval_data["version_id"]
        is_screening = eval_data["validator_hotkey"].startswith("i-0")
        
        if is_screening:
            await self._update_agent_status_for_screening(conn, version_id, eval_data)
        else:
            await self._update_agent_status_for_validation(conn, version_id)
    
    async def _update_agent_status_for_screening(self, conn: asyncpg.Connection, version_id: str, eval_data):
        """Update agent status based on screening evaluation"""
        eval_status = EvaluationStatus.from_string(eval_data["status"])
        
        if eval_status == EvaluationStatus.running:
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.screening.value, version_id)
        elif eval_status == EvaluationStatus.completed:
            if eval_data["score"] and eval_data["score"] >= SCREENING_THRESHOLD:
                # Passed screening - set to waiting and create evaluations for validators
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.waiting.value, version_id)
                await self._create_evaluations_for_waiting_agent(conn, version_id)
                # Notify validators
                await self.ws_manager.send_to_all_validators("evaluation-available", {"version_id": version_id})
            else:
                # Failed screening
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.failed_screening.value, version_id)
        elif eval_status in [EvaluationStatus.error, EvaluationStatus.replaced]:
            # Reset to awaiting screening
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.awaiting_screening.value, version_id)
    
    async def _update_agent_status_for_validation(self, conn: asyncpg.Connection, version_id: str):
        """Update agent status based on validation evaluations"""
        # Check agent's current evaluation state
        if await self.should_agent_be_waiting(conn, version_id):
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.waiting.value, version_id)
        elif await self.should_agent_be_scored(conn, version_id):
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.scored.value, version_id)
        else:
            # Must be evaluating
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.evaluating.value, version_id)

    # Transition handlers
    async def _start(self, conn: asyncpg.Connection, evaluation_id: str, to_state, **context):
        """Evaluation starts running"""
        await conn.execute("""
            UPDATE evaluations SET status = $1, started_at = NOW() WHERE evaluation_id = $2
        """, to_state.value, evaluation_id)
    
    async def _complete(self, conn: asyncpg.Connection, evaluation_id: str, to_state, **context):
        """Evaluation completes successfully"""
        await conn.execute("""
            UPDATE evaluations SET status = $1, finished_at = NOW() WHERE evaluation_id = $2
        """, to_state.value, evaluation_id)
    
    async def _error(self, conn: asyncpg.Connection, evaluation_id: str, to_state,
                    reason: str = "unknown", **context):
        """Evaluation encounters an error"""
        await conn.execute("""
            UPDATE evaluations SET status = $1, finished_at = NOW(), terminated_reason = $2 
            WHERE evaluation_id = $3
        """, to_state.value, reason, evaluation_id)
        # Cancel associated evaluation runs
        await conn.execute("""
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW()
            WHERE evaluation_id = $1 AND status != 'cancelled'
        """, evaluation_id)
    
    async def _replace(self, conn: asyncpg.Connection, evaluation_id: str, to_state, **context):
        """Evaluation is replaced (agent was replaced)"""
        await conn.execute("""
            UPDATE evaluations SET status = $1, finished_at = NOW() WHERE evaluation_id = $2
        """, to_state.value, evaluation_id)
        # Cancel associated evaluation runs
        await conn.execute("""
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW()
            WHERE evaluation_id = $1 AND status != 'cancelled'
        """, evaluation_id)

    async def _reset_to_waiting(self, conn: asyncpg.Connection, evaluation_id: str, to_state, **context):
        """Reset evaluation back to waiting (e.g., due to validator disconnect)"""
        await conn.execute("""
            UPDATE evaluations SET status = $1, started_at = NULL WHERE evaluation_id = $2
        """, to_state.value, evaluation_id)
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

    async def _create_evaluations_for_waiting_agent(self, conn: asyncpg.Connection, version_id: str):
        """Create evaluations for all connected validators for a waiting agent"""
        # Get all connected validator hotkeys
        validator_hotkeys = await self.ws_manager.get_connected_validator_hotkeys()
        
        # Create evaluations for each validator
        for hotkey in validator_hotkeys:
            await self.create_evaluation_for_validator(conn, version_id, hotkey)

    # Core controller methods (previously in AgentStateMachine)
    async def agent_upload(self, screener: 'Screener', miner_hotkey: str, agent_name: str, version_num: int, version_id: str) -> bool:
        """Replace old agents, create new agent, start screening with provided screener"""
        async with self.atomic_transaction() as conn:
            # Block if miner has running evaluations
            if await conn.fetchval("SELECT COUNT(*) FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id WHERE ma.miner_hotkey = $1 AND e.status = 'running'", miner_hotkey):
                return False
            
            # Replace all old agents for this miner
            old_agents = await conn.fetch("SELECT version_id FROM miner_agents WHERE miner_hotkey = $1 AND status != 'replaced'", miner_hotkey)
            for agent in old_agents:
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.replaced.value, agent["version_id"])
                await self.replace_for_agent(conn, agent["version_id"])
            
            # Create new agent which is awaiting_screening
            await conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES ($1, $2, $3, $4, NOW(), $5)
            """, version_id, miner_hotkey, agent_name, version_num, AgentStatus.awaiting_screening.value)
            
            # Assign to the provided screener that handled the upload
            eval_id = await self.create_screening(conn, version_id, screener.hotkey)
            
            from api.src.backend.entities import MinerAgent
            from datetime import datetime
            agent_data = MinerAgent(
                version_id=version_id,
                miner_hotkey=miner_hotkey,
                agent_name=agent_name,
                version_num=version_num,
                created_at=datetime.now(),
                status=AgentStatus.awaiting_screening
            )
            
            success = await self.ws_manager.send_to_client(screener, {
                "event": "screen-agent",
                "evaluation_id": eval_id,
                "agent_version": agent_data.model_dump(mode='json')
            })
            
            if success:
                await self.screener_machine.set_running_screening(screener, eval_id, agent_data.agent_name)
            
            return success

    async def screener_connect(self, screener: 'Screener') -> bool:
        """Assign screener to next awaiting agent if available"""
        async with self.atomic_transaction() as conn:
            from api.src.backend.queries.agents import get_agent_by_version_id
            
            await self.screener_machine.set_available(screener)
            logger.info(f"Screener {screener.hotkey} connected")
            
            # Find next awaiting agent and create screening evaluation
            awaiting_agent = await conn.fetchrow("""
                SELECT version_id FROM miner_agents 
                WHERE status = 'awaiting_screening' 
                ORDER BY created_at ASC 
                LIMIT 1
            """)
            
            if not awaiting_agent:
                return True
                
            eval_id = await self.create_screening(conn, awaiting_agent["version_id"], screener.hotkey)
            agent_data = await get_agent_by_version_id(awaiting_agent["version_id"])
            
            success = await self.ws_manager.send_to_client(screener, {
                "event": "screen-agent",
                "evaluation_id": eval_id,
                "agent_version": agent_data.model_dump(mode='json')
            })
            
            if success:
                await self.screener_machine.set_running_screening(screener, eval_id, agent_data.agent_name)
                
            return success

    async def screener_disconnect(self, screener_hotkey: str):
        """Reset screening work: cancel evaluation, reset agent to awaiting_screening"""
        async with self.atomic_transaction() as conn:
            # Handle both running and waiting evaluations for this screener
            active_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id, e.status
                FROM evaluations e
                WHERE e.validator_hotkey = $1 AND e.status IN ('running', 'waiting') AND e.validator_hotkey LIKE 'i-0%'
            """, screener_hotkey)
            
            for eval_row in active_evals:
                current_status = EvaluationStatus.from_string(eval_row["status"])
                await self.transition(conn, eval_row["evaluation_id"], current_status, EvaluationStatus.error, reason="Disconnected from screener")

    async def validator_connect(self, validator: 'Validator') -> bool:
        async with self.atomic_transaction() as conn:
            await self.validator_machine.set_available(validator)

            # Create evaluations for agents that need them
            agents_needing_eval = await conn.fetch("""
                SELECT version_id FROM miner_agents 
                WHERE status IN ('waiting', 'evaluating') 
                AND NOT EXISTS (
                    SELECT 1 FROM evaluations 
                    WHERE version_id = miner_agents.version_id 
                    AND validator_hotkey = $1
                )
                ORDER BY created_at ASC
            """, validator.hotkey)
            
            for agent in agents_needing_eval:
                await self.create_evaluation_for_validator(
                    conn, agent["version_id"], validator.hotkey
                )
            
            # Check if there are any waiting evaluations for this validator
            waiting_eval = await conn.fetchrow("""
                SELECT evaluation_id FROM evaluations 
                WHERE validator_hotkey = $1 AND status = 'waiting'
                ORDER BY created_at ASC
                LIMIT 1
            """, validator.hotkey)
            
            if waiting_eval:
                return await self.ws_manager.send_to_client(validator, {"event": "evaluation-available"})
            
            return True

    async def validator_disconnect(self, validator_hotkey: str):
        """Reset running evaluation: eval back to waiting, agent back to waiting if no other evals"""
        async with self.atomic_transaction() as conn:
            running_evals_for_validator = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id
                FROM evaluations e
                WHERE e.validator_hotkey = $1 AND e.status = 'running' AND e.validator_hotkey NOT LIKE 'i-0%'
            """, validator_hotkey)
            
            for eval_row in running_evals_for_validator:
                await self.transition(conn, eval_row["evaluation_id"],
                                    EvaluationStatus.running, EvaluationStatus.waiting)

    async def start_screening(self, screener: 'Screener', evaluation_id: str) -> bool:
        async with self.atomic_transaction() as conn:
            # Get evaluation and agent info
            eval_data = await conn.fetchrow("""
                SELECT e.version_id, ma.agent_name, ma.status as agent_status
                FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_data:
                return False
            
            agent_state = AgentStatus.from_string(eval_data["agent_status"])
            
            # If the agent isn't awaiting_screening, it can't start screening
            if agent_state != AgentStatus.awaiting_screening:
                return False
            
            # Start the evaluation
            if not await self.start_evaluation(conn, evaluation_id, screener.hotkey):
                return False
            
            await self.screener_machine.set_running_screening(screener, evaluation_id, eval_data["agent_name"])
            return True

    async def finish_screening(self, screener: 'Screener', evaluation_id: str) -> bool:
        async with self.atomic_transaction() as conn:
            # Get evaluation and agent info
            eval_data = await conn.fetchrow("""
                SELECT e.version_id, ma.status as agent_status, e.score
                FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_data:
                return False
            
            agent_state = AgentStatus.from_string(eval_data["agent_status"])
            
            # If the agent isn't screening, it can't finish screening
            if agent_state != AgentStatus.screening:
                return False
            
            await self.transition(conn, evaluation_id, EvaluationStatus.running, EvaluationStatus.completed)
            await self.screener_machine.set_available(screener)
            
            # Try to assign the screener to the next available screening
            await self.screener_connect(screener)
            
            return True

    async def start_validator_evaluation(self, validator: 'Validator', evaluation_id: str) -> bool:
        async with self.atomic_transaction() as conn:
            # Get evaluation and agent info
            eval_data = await conn.fetchrow("""
                SELECT e.version_id, ma.status as agent_status, ma.agent_name
                FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_data:
                return False
            
            agent_state = AgentStatus.from_string(eval_data["agent_status"])
            
            # If the agent isn't waiting or evaluating, it can't transition to evaluating
            if agent_state not in [AgentStatus.waiting, AgentStatus.evaluating]:
                return False
            
            # Start the evaluation
            if not await self.start_evaluation(conn, evaluation_id, validator.hotkey):
                return False
            
            await self.validator_machine.set_running_evaluation(validator, evaluation_id, eval_data["agent_name"])
            return True

    async def finish_validator_evaluation(self, validator: 'Validator', evaluation_id: str, errored: bool = False, reason: str = None) -> bool:
        async with self.atomic_transaction() as conn:
            # Get evaluation and agent info
            eval_data = await conn.fetchrow("""
                SELECT e.version_id, ma.status as agent_status
                FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_data:
                return False
            
            agent_state = AgentStatus.from_string(eval_data["agent_status"])
            
            # If the agent isn't evaluating, it can't finish
            if agent_state != AgentStatus.evaluating:
                return False
            
            if errored:
                await self.transition(conn, evaluation_id, EvaluationStatus.running, EvaluationStatus.error, reason=reason)
            else:
                await self.transition(conn, evaluation_id, EvaluationStatus.running, EvaluationStatus.completed)
            
            await self.validator_machine.set_available(validator)
            return True

    async def application_startup(self):
        """Fix broken states from previous shutdown"""
        async with self.atomic_transaction() as conn:
            logger.info("Starting application startup recovery")
            
            # 1. Handle running evaluations based on type
            running_evals = await conn.fetch("""
                SELECT evaluation_id, validator_hotkey 
                FROM evaluations 
                WHERE status = 'running'
            """)
            
            for eval_row in running_evals:
                if eval_row["validator_hotkey"].startswith("i-0"):
                    # Screener evaluation - error it since screener disconnected
                    await self.transition(conn, eval_row["evaluation_id"], EvaluationStatus.running, EvaluationStatus.error, reason="Disconnected from screener")
                else:
                    # Validator evaluation - reset to waiting since validator disconnected
                    await self.transition(conn, eval_row["evaluation_id"], 
                                       EvaluationStatus.running, EvaluationStatus.waiting)
            
            # 2. Cancel all waiting screener evaluations (screeners disconnect on restart)
            waiting_screener_evals = await conn.fetch("""
                SELECT evaluation_id, version_id 
                FROM evaluations 
                WHERE status = 'waiting' AND validator_hotkey LIKE 'i-0%'
            """)
            
            for eval_row in waiting_screener_evals:
                await self.transition(conn, eval_row["evaluation_id"], EvaluationStatus.waiting, EvaluationStatus.error, reason="Disconnected from screener")
            
            # 3. Reset screening agents (screeners disconnect on restart)
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening' WHERE status = 'screening'")
            
            # 4. Fix evaluating agents based on their evaluation state
            evaluating_agents = await conn.fetch("SELECT version_id FROM miner_agents WHERE status = 'evaluating'")
            for agent in evaluating_agents:
                await self._update_agent_status_for_validation(conn, agent["version_id"])
            
            logger.info("Application startup recovery completed")

    async def re_evaluate_approved_agents(self) -> List:
        """Re-evaluate all approved agents: reset to awaiting_screening"""
        async with self.atomic_transaction() as conn:
            from api.src.backend.entities import MinerAgent
            
            agent_data = await conn.fetch("""
                UPDATE miner_agents 
                SET status = 'awaiting_screening'
                WHERE version_id IN (SELECT version_id FROM approved_version_ids)
                AND status != 'replaced'
                RETURNING *
            """)

            agents = [MinerAgent(**agent) for agent in agent_data]

            # Assign each agent to the first available screener 
            for agent in agents:
                available_screener = await self.ws_manager.get_available_screener()
                if not available_screener:
                    break

                eval_id = await self.create_screening(conn, agent.version_id, available_screener.hotkey)
                success = await self.ws_manager.send_to_client(available_screener, {
                    "event": "screen-agent",
                    "evaluation_id": eval_id,
                    "agent_version": agent.model_dump(mode='json')
                })
                
                if success:
                    await self.screener_machine.set_running_screening(available_screener, eval_id, agent.agent_name)
            
            logger.info(f"Reset {len(agents)} approved agents to awaiting_screening status")
            
            return agents
    
    # Existing evaluation management methods
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