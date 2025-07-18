import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, TYPE_CHECKING, List
from contextlib import asynccontextmanager
import asyncpg
import uuid

from api.src.backend.db_manager import get_db_connection
from api.src.backend.entities import EvaluationStatus, AgentStatus
from api.src.backend.queries.evaluation_sets import get_latest_set_id
from api.src.utils.config import SCREENING_THRESHOLD

if TYPE_CHECKING:
    from api.src.backend.entities import Client
    from api.src.socket.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

class StateValidator:
    AGENT_TRANSITIONS = {
        AgentStatus.awaiting_screening: {AgentStatus.screening, AgentStatus.replaced},
        AgentStatus.screening: {AgentStatus.failed_screening, AgentStatus.waiting, AgentStatus.replaced},
        AgentStatus.failed_screening: {AgentStatus.awaiting_screening},
        AgentStatus.waiting: {AgentStatus.evaluating, AgentStatus.replaced},
        AgentStatus.evaluating: {AgentStatus.scored, AgentStatus.replaced},
        AgentStatus.scored: {AgentStatus.awaiting_screening},
        AgentStatus.replaced: {}
    }
    
    EVALUATION_TRANSITIONS = {
        EvaluationStatus.waiting: {EvaluationStatus.running, EvaluationStatus.error, EvaluationStatus.replaced},
        EvaluationStatus.running: {EvaluationStatus.completed, EvaluationStatus.error, EvaluationStatus.replaced},
        EvaluationStatus.completed: {},
        EvaluationStatus.error: {},
        EvaluationStatus.replaced: {}
    }
    
    @classmethod
    def validate_agent_transition(cls, from_state: AgentStatus, to_state: AgentStatus) -> bool:
        return to_state in cls.AGENT_TRANSITIONS.get(from_state, set())
    
    @classmethod
    def validate_evaluation_transition(cls, from_state: EvaluationStatus, to_state: EvaluationStatus) -> bool:
        return to_state in cls.EVALUATION_TRANSITIONS.get(from_state, set())

class StateTransitionError(Exception):
    pass

class ConcurrencyError(Exception):
    pass

class EvaluationStateMachine:
    _instance = None
    _ws_manager = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized') or not self._initialized:
            from api.src.socket.websocket_manager import WebSocketManager
            self.validator = StateValidator()
            self.ws_manager = WebSocketManager.get_instance()
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

    async def _check_evaluations_still_running(self, conn: asyncpg.Connection, version_id: str, check_waiting: bool = True) -> bool:
        """
        Check if there are still running (and optionally waiting) evaluations for a version.
        Returns True if evaluations are still running, False if all are complete.
        """
        if check_waiting:
            result = await conn.fetchrow("""
                SELECT COUNT(*) as count FROM evaluations 
                WHERE version_id = $1 AND status IN ($2, $3)
            """, version_id, EvaluationStatus.waiting.value, EvaluationStatus.running.value)
        else:
            result = await conn.fetchrow("""
                SELECT COUNT(*) as count FROM evaluations 
                WHERE version_id = $1 AND status = $2
            """, version_id, EvaluationStatus.running.value)
        
        return result["count"] > 0

    async def _update_agent_to_scored_if_no_running_evals(self, conn: asyncpg.Connection, version_id: str, check_waiting: bool = True):
        """
        Update agent status to scored if no evaluations are still running for this version.
        """
        still_running = await self._check_evaluations_still_running(conn, version_id, check_waiting)
        
        if not still_running:
            await conn.execute("""
                UPDATE miner_agents 
                SET status = $1 
                WHERE version_id = $2
            """, AgentStatus.scored.value, version_id)

    async def _create_evaluation(self, conn: asyncpg.Connection, version_id: str, validator_hotkey: str) -> str:
        """
        Create a single evaluation record in the database.
        Returns the evaluation_id.
        """
        evaluation_id = str(uuid.uuid4())
        current_set_id = await get_latest_set_id()
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
        """, evaluation_id, version_id, validator_hotkey, current_set_id, EvaluationStatus.waiting.value)
        
        return evaluation_id

    async def _create_and_assign_screening_evaluation(self, conn: asyncpg.Connection, version_id: str, agent_data=None) -> bool:
        """
        Create a screening evaluation and assign it to an available screener.
        Returns True if successful, False otherwise.
        """
        from api.src.backend.queries.agents import get_agent_by_version_id
        
        screener = await self.ws_manager.get_available_screener()
        if not screener:
            logger.warning(f"No available screener for agent {version_id}")
            return False
        
        evaluation_id = await self._create_evaluation(conn, version_id, screener.hotkey)
        
        # Get agent data if not provided
        if not agent_data:
            miner_agent = await get_agent_by_version_id(version_id)
            agent_data = miner_agent.model_dump(mode='json')
        
        # Assign to screener
        success = await self.ws_manager.send_to_client(screener, {
            "event": "screen-agent",
            "evaluation_id": str(evaluation_id),
            "agent_version": agent_data
        })
        
        if success:
            screener.status = "busy"
            logger.info(f"Successfully assigned screening evaluation {evaluation_id} to screener {screener.hotkey}")
            return True
        else:
            logger.warning(f"Failed to assign screening evaluation {evaluation_id} to screener {screener.hotkey}")
            return False

    async def upload_new_agent(self, miner_hotkey: str, agent_name: str, version_num: int) -> Tuple[str, bool]:
        async with self.atomic_transaction() as conn:
            # Check if miner has any running evaluations
            result = await conn.fetchrow("""
                SELECT COUNT(*) as count FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE ma.miner_hotkey = $1 AND e.status = $2
            """, miner_hotkey, EvaluationStatus.running.value)
            if result["count"] > 0:
                return None, False
            
            await self._replace_older_versions(conn, miner_hotkey)
            
            version_id = str(uuid.uuid4())
            await conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES ($1, $2, $3, $4, NOW(), $5)
            """, version_id, miner_hotkey, agent_name, version_num, AgentStatus.awaiting_screening.value)
            
            # Create and assign screening evaluation
            success = await self._create_and_assign_screening_evaluation(conn, version_id)
            if not success:
                return None, False
            
            return version_id, True
    
    async def _replace_older_versions(self, conn: asyncpg.Connection, miner_hotkey: str):
        older_versions = await conn.fetch("""
            SELECT version_id, status FROM miner_agents 
            WHERE miner_hotkey = $1 AND status != 'replaced'
        """, miner_hotkey)
        
        for version_row in older_versions:
            version_id = version_row["version_id"]
            current_status = version_row["status"]
            current_state = AgentStatus.from_string(current_status)
            
            if current_state == AgentStatus.replaced:
                continue
            
            if not self.validator.validate_agent_transition(current_state, AgentStatus.replaced):
                continue
            
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW() 
                WHERE version_id = $2 AND status IN ($3, $4)
            """, EvaluationStatus.replaced.value, version_id, EvaluationStatus.waiting.value, EvaluationStatus.running.value)
            
            await conn.execute("""
                UPDATE miner_agents 
                SET status = $1 
                WHERE version_id = $2
            """, AgentStatus.replaced.value, version_id)
    
    async def start_screening(self, evaluation_id: str, screener_hotkey: str) -> bool:
        async with self.atomic_transaction() as conn:
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, ma.status as agent_status
                FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1 AND e.validator_hotkey = $2
            """, evaluation_id, screener_hotkey)
            
            if not eval_info:
                return False
            
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            agent_state = AgentStatus.from_string(eval_info["agent_status"])
            
            if eval_state != EvaluationStatus.waiting or agent_state != AgentStatus.awaiting_screening:
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
            
            return True
    
    async def finish_screening(self, evaluation_id: str, score: float) -> bool:
        async with self.atomic_transaction() as conn:
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, e.validator_hotkey
                FROM evaluations e
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_info:
                return False
            
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.running:
                return False
            
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW(), score = $2
                WHERE evaluation_id = $3
            """, EvaluationStatus.completed.value, score, evaluation_id)
            
            if score >= SCREENING_THRESHOLD:
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1, score = $2
                    WHERE version_id = $3
                """, AgentStatus.waiting.value, score, eval_info["version_id"])
                
                await self._create_validator_evaluations(conn, eval_info["version_id"])
                
                await self.ws_manager.send_to_all_validators("evaluation-available", {
                    "version_id": eval_info["version_id"]
                })
            else:
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1, score = $2
                    WHERE version_id = $3
                """, AgentStatus.failed_screening.value, score, eval_info["version_id"])
            
            return True
    
    async def start_evaluation(self, evaluation_id: str, hotkey: str) -> bool:
        async with self.atomic_transaction() as conn:
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, ma.status as agent_status
                FROM evaluations e
                JOIN miner_agents ma ON e.version_id = ma.version_id
                WHERE e.evaluation_id = $1 AND e.validator_hotkey = $2
            """, evaluation_id, hotkey)
            
            if not eval_info:
                return False
            
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.waiting:
                return False
            
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, started_at = NOW() 
                WHERE evaluation_id = $2
            """, EvaluationStatus.running.value, evaluation_id)
            
            agent_state = AgentStatus.from_string(eval_info["agent_status"])
            if agent_state == AgentStatus.waiting:
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1 
                    WHERE version_id = $2
                """, AgentStatus.evaluating.value, eval_info["version_id"])
            
            return True
    
    async def finish_evaluation(self, evaluation_id: str, score: float, errored: bool = False) -> bool:
        async with self.atomic_transaction() as conn:
            eval_info = await conn.fetchrow("""
                SELECT e.evaluation_id, e.version_id, e.status, e.validator_hotkey
                FROM evaluations e
                WHERE e.evaluation_id = $1
            """, evaluation_id)
            
            if not eval_info:
                return False
            
            eval_state = EvaluationStatus.from_string(eval_info["status"])
            if eval_state != EvaluationStatus.running:
                return False
            
            final_status = "error" if errored else "completed"
            await conn.execute("""
                UPDATE evaluations 
                SET status = $1, finished_at = NOW(), score = $2
                WHERE evaluation_id = $3
            """, final_status, score, evaluation_id)
            
            await self._update_agent_to_scored_if_no_running_evals(conn, eval_info["version_id"])
            
            return True
    
    async def handle_screener_disconnect(self, screener_hotkey: str):
        async with self.atomic_transaction() as conn:
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
                
                await conn.execute("""
                    UPDATE evaluations 
                    SET status = $1, started_at = NULL
                    WHERE evaluation_id = $2
                """, EvaluationStatus.waiting.value, evaluation_id)
                
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = $1
                    WHERE version_id = $2
                """, AgentStatus.awaiting_screening.value, version_id)
                
                new_screener = await self.ws_manager.get_available_screener()
                if new_screener:
                    await self.ws_manager.send_to_client(new_screener, {
                        "type": "screen-agent",
                        "evaluation_id": evaluation_id,
                        "version_id": version_id
                    })
    
    async def handle_validator_disconnect(self, hotkey: str):
        async with self.atomic_transaction() as conn:
            running_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id
                FROM evaluations e
                WHERE e.validator_hotkey = $1 
                AND e.status = $2
                AND e.validator_hotkey NOT LIKE 'i-0%'
            """, hotkey, EvaluationStatus.running.value)
            
            for eval_row in running_evals:
                evaluation_id = eval_row["evaluation_id"]
                version_id = eval_row["version_id"]
                
                await conn.execute("""
                    UPDATE evaluations 
                    SET status = $1, started_at = NULL
                    WHERE evaluation_id = $2
                """, EvaluationStatus.waiting.value, evaluation_id)
                
                # Only check running evaluations, not waiting ones, and set to waiting (not scored)
                still_running = await self._check_evaluations_still_running(conn, version_id, check_waiting=False)
                
                if not still_running:
                    await conn.execute("""
                        UPDATE miner_agents 
                        SET status = $1
                        WHERE version_id = $2
                    """, AgentStatus.waiting.value, version_id)
                
                await self.ws_manager.send_to_all_validators("evaluation-available", {
                    "evaluation_id": evaluation_id,
                    "version_id": version_id
                })
    
    async def handle_timeouts(self):
        async with self.atomic_transaction() as conn:
            now = datetime.now(timezone.utc)
            
            screening_timeout = now - timedelta(minutes=10)
            screening_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey
                FROM evaluations e
                WHERE e.status = $1 
                AND e.validator_hotkey LIKE 'i-0%'
                AND e.started_at < $2
            """, EvaluationStatus.running.value, screening_timeout)
            
            for eval_row in screening_evals:
                await self._timeout_evaluation(conn, eval_row["evaluation_id"], eval_row["version_id"])
            
            eval_timeout = now - timedelta(minutes=60)
            regular_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey
                FROM evaluations e
                WHERE e.status = $1 
                AND e.validator_hotkey NOT LIKE 'i-0%'
                AND e.started_at < $2
            """, EvaluationStatus.running.value, eval_timeout)
            
            for eval_row in regular_evals:
                await self._timeout_evaluation(conn, eval_row["evaluation_id"], eval_row["version_id"])

    async def handle_screener_connect(self, screener: 'Client') -> bool:
        """Handle screener connection and assign work if available"""
        async with self.atomic_transaction() as conn:
            from api.src.backend.queries.evaluations import get_next_evaluation_for_screener
            from api.src.backend.queries.agents import get_agent_by_version_id
            
            logger.info(f"Screener {screener.hotkey} has connected. Checking if there are agents awaiting screening.")
            
            evaluation = await get_next_evaluation_for_screener()
            if evaluation:
                logger.info(f"Found evaluation {evaluation.evaluation_id} for screener {screener.hotkey}")
                miner_agent = await get_agent_by_version_id(evaluation.version_id)
                
                success = await self.ws_manager.send_to_client(screener, {
                    "event": "screen-agent",
                    "evaluation_id": str(evaluation.evaluation_id),
                    "agent_version": miner_agent.model_dump(mode='json')
                })
                
                if success:
                    # Update screener status to busy
                    screener.status = "busy"
                    logger.info(f"Successfully sent evaluation {evaluation.evaluation_id} to screener {screener.hotkey}")
                    return True
                else:
                    logger.warning(f"Failed to send evaluation to screener {screener.hotkey}")
                    return False
            else:
                logger.info(f"No evaluations available for screener {screener.hotkey}")
                return True

    async def handle_validator_connect(self, validator: 'Client') -> bool:
        """Handle validator connection and notify if work is available"""
        async with self.atomic_transaction() as conn:
            from api.src.backend.queries.evaluations import get_next_evaluation_for_validator
            
            logger.info(f"Validator {validator.hotkey} has connected. Checking for available evaluations.")
            
            next_evaluation = await get_next_evaluation_for_validator(validator.hotkey)
            if next_evaluation:
                logger.debug(f"Found evaluation {next_evaluation.evaluation_id} for validator {validator.hotkey}")
                
                success = await self.ws_manager.send_to_client(validator, {
                    "event": "evaluation-available"
                })
                
                if success:
                    logger.info(f"Successfully notified validator {validator.hotkey} of available evaluation")
                    return True
                else:
                    logger.warning(f"Failed to notify validator {validator.hotkey}")
                    return False
            else:
                logger.info(f"No evaluations available for validator {validator.hotkey}")
                return True

    async def _timeout_evaluation(self, conn: asyncpg.Connection, evaluation_id: str, version_id: str):
        await conn.execute("""
            UPDATE evaluations 
            SET status = $1, terminated_reason = $2, finished_at = NOW()
            WHERE evaluation_id = $3
        """, EvaluationStatus.error.value, "timed out", evaluation_id)
        
        await self._update_agent_to_scored_if_no_running_evals(conn, version_id)
    
    async def _create_validator_evaluations(self, conn: asyncpg.Connection, version_id: str):
        hotkeys = await self.ws_manager.get_connected_hotkeys()
        
        for hotkey in hotkeys:
            existing = await conn.fetchrow("""
                SELECT evaluation_id FROM evaluations 
                WHERE version_id = $1 AND validator_hotkey = $2
            """, version_id, hotkey)
            
            if existing:
                continue
            
            await self._create_evaluation(conn, version_id, hotkey)

    async def re_evaluate_approved_agents(self) -> List:
        """
        Re-evaluate all approved agents with the newest evaluation set
        """
        async with self.atomic_transaction() as conn:
            from api.src.backend.queries.agents import set_approved_agents_to_awaiting_screening
            
            # Set approved agents to awaiting screening
            agents_to_re_evaluate = await set_approved_agents_to_awaiting_screening()
            
            if not agents_to_re_evaluate:
                logger.info("No approved agents found for re-evaluation")
                return []
            
            logger.info(f"Setting {len(agents_to_re_evaluate)} approved agents to awaiting screening")
            
            # Create and assign screening evaluations for each agent
            for agent in agents_to_re_evaluate:
                success = await self._create_and_assign_screening_evaluation(
                    conn, 
                    agent.version_id, 
                    agent.model_dump(mode='json')
                )
                if not success:
                    logger.warning(f"Failed to create screening evaluation for agent {agent.version_id}")
            
            return agents_to_re_evaluate
    