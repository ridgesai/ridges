import logging
from typing import Optional, Tuple, List
from contextlib import asynccontextmanager
import asyncpg
import uuid

from api.src.backend.db_manager import get_db_connection
from api.src.backend.entities import AgentStatus, EvaluationStatus, Screener, Validator
from api.src.backend.evaluation_machine import EvaluationStateMachine
from api.src.utils.config import SCREENING_THRESHOLD

logger = logging.getLogger(__name__)

class AgentStateMachine:
    """Agent lifecycle controller - handles the 6 core operations"""
    
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
            self.evaluation_machine = EvaluationStateMachine()
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
    
    
    async def _assign_agent_to_screener(self, conn: asyncpg.Connection, version_id: str, screener) -> bool:
        """Try to assign agent to screener"""
        eval_id = await self.evaluation_machine.create_screening(conn, version_id, screener.hotkey)
        
        from api.src.backend.queries.agents import get_agent_by_version_id
        agent_data = await get_agent_by_version_id(version_id)
        
        success = await self.ws_manager.send_to_client(screener, {
            "event": "screen-agent",
            "evaluation_id": eval_id,
            "agent_version": agent_data.model_dump(mode='json')
        })
        
        if success:
            screener.status = f"Screening agent {agent_data.agent_name} with evaluation {eval_id}"
        return success
    
    async def _create_evaluations_for_waiting_agent(self, conn: asyncpg.Connection, version_id: str):
        """Create evaluations for all connected validators for a waiting agent"""
        # Get all connected validator hotkeys
        validator_hotkeys = await self.ws_manager.get_connected_validator_hotkeys()
        
        # Create evaluations for each validator
        for hotkey in validator_hotkeys:
            await self.evaluation_machine.create_evaluation_for_validator(conn, version_id, hotkey)

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
                    await self.evaluation_machine.error_with_reason(conn, eval_row["evaluation_id"], "Disconnected from screener")
                else:
                    # Validator evaluation - reset to waiting since validator disconnected
                    await self.evaluation_machine.transition(conn, eval_row["evaluation_id"], 
                                                           EvaluationStatus.running, EvaluationStatus.waiting)
            
            # 2. Reset screening agents (screeners disconnect on restart)
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening' WHERE status = 'screening'")
            
            # 3. Fix evaluating agents based on their evaluation state
            evaluating_agents = await conn.fetch("SELECT version_id FROM miner_agents WHERE status = 'evaluating'")
            for agent in evaluating_agents:
                if await self.evaluation_machine.should_agent_be_waiting(conn, agent["version_id"]):
                    await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                     AgentStatus.waiting.value, agent["version_id"])
                elif await self.evaluation_machine.should_agent_be_scored(conn, agent["version_id"]):
                    await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                     AgentStatus.scored.value, agent["version_id"])
            
            logger.info("Application startup recovery completed")

    async def agent_upload(self, screener: 'Screener', miner_hotkey: str, agent_name: str, version_num: int, version_id: str) -> bool:
        """Replace old agents, create new agent, start screening with provided screener"""
        async with self.atomic_transaction() as conn:
            # Block if miner has running evaluations
            if await conn.fetchval("SELECT COUNT(*) FROM evaluations e JOIN miner_agents ma ON e.version_id = ma.version_id WHERE ma.miner_hotkey = $1 AND e.status = 'running'", miner_hotkey):
                return None, False
            
            # Replace all old agents for this miner
            old_agents = await conn.fetch("SELECT version_id FROM miner_agents WHERE miner_hotkey = $1 AND status != 'replaced'", miner_hotkey)
            for agent in old_agents:
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.replaced.value, agent["version_id"])
                await self.evaluation_machine.replace_for_agent(conn, agent["version_id"])
            
            # Create new agent which is awaiting_screening
            await conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES ($1, $2, $3, $4, NOW(), $5)
            """, version_id, miner_hotkey, agent_name, version_num, AgentStatus.awaiting_screening.value)
            
            # Assign to provided screener
            eval_id = await self.evaluation_machine.create_screening(conn, version_id, screener.hotkey)
            
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
                screener.status = f"Screening agent {agent_data.agent_name} with evaluation {eval_id}"
            
            return success

    async def screener_connect(self, screener) -> bool:
        """Assign screener to next awaiting agent if available"""
        from api.src.backend.queries.evaluations import get_next_evaluation_for_screener
        from api.src.backend.queries.agents import get_agent_by_version_id
        
        screener.status = "available"
        logger.info(f"Screener {screener.hotkey} connected")
        evaluation = await get_next_evaluation_for_screener(screener.hotkey)
        
        if not evaluation:
            return True
            
        agent_data = await get_agent_by_version_id(evaluation.version_id)
        success = await self.ws_manager.send_to_client(screener, {
            "event": "screen-agent",
            "evaluation_id": str(evaluation.evaluation_id),
            "agent_version": agent_data.model_dump(mode='json')
        })
        
        if success:
            screener.status = f"Screening agent {agent_data.agent_name} with evaluation {evaluation.evaluation_id}"
            
        return success

    async def screener_disconnect(self, screener_hotkey: str):
        """Reset screening work: error evaluation, agent back to awaiting_screening"""
        async with self.atomic_transaction() as conn:
            running_evals = await conn.fetch("""
                SELECT e.evaluation_id, e.version_id
                FROM evaluations e
                WHERE e.validator_hotkey = $1 AND e.status = 'running' AND e.validator_hotkey LIKE 'i-0%'
            """, screener_hotkey)
            
            for eval_row in running_evals:
                await self.evaluation_machine.transition(conn, eval_row["evaluation_id"], 
                                                        EvaluationStatus.running, EvaluationStatus.error, 
                                                        reason="Disconnected from screener")
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.awaiting_screening.value, eval_row["version_id"])

    async def validator_connect(self, validator: 'Validator') -> bool:
        async with self.atomic_transaction() as conn:
            validator.status = "available"

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
                await self.evaluation_machine.create_evaluation_for_validator(
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
                await self.evaluation_machine.transition(conn, eval_row["evaluation_id"],
                                                        EvaluationStatus.running, EvaluationStatus.waiting)
                
                # Update agent state if needed based on evaluation state
                if await self.evaluation_machine.should_agent_be_waiting(conn, eval_row["version_id"]):
                    await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                     AgentStatus.waiting.value, eval_row["version_id"])
                
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
            if not await self.evaluation_machine.start_evaluation(conn, evaluation_id, screener.hotkey):
                return False
            
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.screening.value, eval_data["version_id"])
            
            screener.status = f"Screening agent {eval_data['agent_name']} with evaluation {evaluation_id}"
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
            
            await self.evaluation_machine.finish(conn, evaluation_id)
            
            if eval_data["score"] >= SCREENING_THRESHOLD:
                # Agent passed screening - set to waiting and create evaluations for connected validators
                logger.info(f"Screening {evaluation_id} passed with score {eval_data['score']}")
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.waiting.value, eval_data["version_id"])
                
                # Create evaluations for all connected validators
                await self._create_evaluations_for_waiting_agent(conn, eval_data["version_id"])
                
                # Notify validators that work is available
                await self.ws_manager.send_to_all_validators("evaluation-available", {"version_id": str(eval_data["version_id"])})
            else:
                logger.info(f"Screening {evaluation_id} failed with score {eval_data['score']}")
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.failed_screening.value, eval_data["version_id"])
            
            screener.status = "available"
            
            # Try to assign the screener to the next available screening
            await self.screener_connect(screener)
            
            return True

    async def start_evaluation(self, validator: 'Validator', evaluation_id: str) -> bool:
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
            if not await self.evaluation_machine.start_evaluation(conn, evaluation_id, validator.hotkey):
                return False
            
            await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                             AgentStatus.evaluating.value, eval_data["version_id"])
            
            validator.status = f"Evaluating agent {eval_data['agent_name']} with evaluation {evaluation_id}"
            return True

    async def finish_evaluation(self, validator: 'Validator', evaluation_id: str, errored: bool = False, reason: str = None) -> bool:
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
                await self.evaluation_machine.error_with_reason(conn, evaluation_id, reason)
            else:
                await self.evaluation_machine.finish(conn, evaluation_id)
            
            if await self.evaluation_machine.should_agent_be_scored(conn, eval_data["version_id"]):
                await conn.execute("UPDATE miner_agents SET status = $1 WHERE version_id = $2", 
                                 AgentStatus.scored.value, eval_data["version_id"])
            
            validator.status = "available"
            return True


    async def re_evaluate_approved_agents(self) -> List:
        """Re-evaluate all approved agents: reset to awaiting_screening, try to assign screeners"""
        async with self.atomic_transaction() as conn:
            agents = await conn.fetch("""
                UPDATE miner_agents 
                SET status = 'awaiting_screening'
                WHERE version_id IN (SELECT version_id FROM approved_version_ids)
                AND status != 'replaced'
                RETURNING *
            """)
            
            for agent in agents:
                screener = await self.ws_manager.get_available_screener()
                if screener:
                    await self._assign_agent_to_screener(conn, agent["version_id"], screener)
            
            return agents