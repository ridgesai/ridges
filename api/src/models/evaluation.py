import logging
import uuid
from typing import Optional
import asyncpg
import asyncio

from api.src.backend.entities import EvaluationStatus, MinerAgent
from api.src.backend.db_manager import get_db_connection, get_transaction
from api.src.models.screener import Screener
from api.src.models.validator import Validator
from api.src.utils.config import SCREENING_THRESHOLD

logger = logging.getLogger(__name__)


class Evaluation:
    """Evaluation model - handles its own lifecycle atomically"""
    
    _lock = asyncio.Lock()

    def __init__(
        self, evaluation_id: str, version_id: str, validator_hotkey: str, set_id: int, status: EvaluationStatus, score: Optional[float] = None
    ):
        self.evaluation_id = evaluation_id
        self.version_id = version_id
        self.validator_hotkey = validator_hotkey
        self.set_id = set_id
        self.status = status
        self.score = score

    @property
    def is_screening(self) -> bool:
        return self.validator_hotkey.startswith("i-0")

    async def start(self, conn: asyncpg.Connection):
        """Start evaluation"""
        await conn.execute("UPDATE evaluations SET status = 'running', started_at = NOW() WHERE evaluation_id = $1", self.evaluation_id)
        self.status = EvaluationStatus.running

        await self._update_agent_status(conn)

    async def finish(self, conn: asyncpg.Connection):
        """Finish evaluation"""
        await conn.execute("UPDATE evaluations SET status = 'completed', finished_at = NOW() WHERE evaluation_id = $1", self.evaluation_id)
        self.status = EvaluationStatus.completed
        self.score = await conn.fetchval("SELECT score FROM evaluations WHERE evaluation_id = $1", self.evaluation_id)

        # Store validators to notify after agent status update
        validators_to_notify = []
        
        if self.is_screening:
            if self.score < SCREENING_THRESHOLD:
                logger.info(f"Screening failed for agent {self.version_id} with score {self.score}")
            else:
                logger.info(f"Screening passed for agent {self.version_id} with score {self.score}")
                from api.src.models.validator import Validator

                # Create evaluation records but don't notify yet
                validators_to_notify = await Validator.get_connected()
                for validator in validators_to_notify:
                    await self.create_for_validator(conn, self.version_id, validator.hotkey)

        await self._update_agent_status(conn)
        
        for validator in validators_to_notify:
            await validator.send_evaluation_available(self.version_id)

    async def error(self, conn: asyncpg.Connection, reason: Optional[str] = None):
        """Error evaluation and reset agent"""
        await conn.execute(
            "UPDATE evaluations SET status = 'error', finished_at = NOW(), terminated_reason = $1 WHERE evaluation_id = $2",
            reason,
            self.evaluation_id,
        )
        self.status = EvaluationStatus.error

        # Cancel all evaluation_runs for this evaluation
        await conn.execute("UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW() WHERE evaluation_id = $1", self.evaluation_id)

        await self._update_agent_status(conn)

    async def reset_to_waiting(self, conn: asyncpg.Connection):
        """Reset running evaluation back to waiting (for disconnections)"""
        await conn.execute("UPDATE evaluations SET status = 'waiting', started_at = NULL WHERE evaluation_id = $1", self.evaluation_id)
        self.status = EvaluationStatus.waiting

        # Reset running evaluation_runs to pending so they can be picked up again
        await conn.execute("UPDATE evaluation_runs SET status = 'cancelled' WHERE evaluation_id = $1 AND status = 'running'", self.evaluation_id)

        await self._update_agent_status(conn)

    async def _update_agent_status(self, conn: asyncpg.Connection):
        """Update agent status based on evaluation state"""
        # Handle screening completion
        if self.is_screening and self.status == EvaluationStatus.completed:
            if self.score is not None and self.score >= SCREENING_THRESHOLD:
                await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE version_id = $1", self.version_id)
            else:
                await conn.execute("UPDATE miner_agents SET status = 'failed_screening' WHERE version_id = $1", self.version_id)
            return

        # Handle screening errors - reset to awaiting_screening
        if self.is_screening and self.status == EvaluationStatus.error:
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening' WHERE version_id = $1", self.version_id)
            return

        # Check for any screening evaluations (waiting OR running) - agent should be in screening state
        screening_count = await conn.fetchval(
            "SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND validator_hotkey LIKE 'i-0%' AND status IN ('waiting', 'running')", 
            self.version_id
        )
        if screening_count > 0:
            await conn.execute("UPDATE miner_agents SET status = 'screening' WHERE version_id = $1", self.version_id)
            return

        # Handle evaluation status transitions for regular evaluations
        if self.status == EvaluationStatus.running and not self.is_screening:
            await conn.execute("UPDATE miner_agents SET status = 'evaluating' WHERE version_id = $1", self.version_id)
            return

        # For other cases, check remaining regular evaluations (non-screening)
        waiting_count = await conn.fetchval(
            "SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status = 'waiting' AND validator_hotkey NOT LIKE 'i-0%'", 
            self.version_id
        )
        running_count = await conn.fetchval(
            "SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status = 'running' AND validator_hotkey NOT LIKE 'i-0%'", 
            self.version_id
        )

        if waiting_count > 0 and running_count == 0:
            await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE version_id = $1", self.version_id)
        elif waiting_count == 0 and running_count == 0:
            await conn.execute("UPDATE miner_agents SET status = 'scored' WHERE version_id = $1", self.version_id)
        else:
            await conn.execute("UPDATE miner_agents SET status = 'evaluating' WHERE version_id = $1", self.version_id)

    
    @staticmethod
    def get_lock():
        """Get the shared lock for evaluation operations"""
        return Evaluation._lock

    @staticmethod
    async def create_for_validator(conn: asyncpg.Connection, version_id: str, validator_hotkey: str) -> str:
        """Create evaluation for validator"""
        set_id = await conn.fetchval("SELECT MAX(set_id) from evaluation_sets")

        # Check if evaluation already exists for this combination
        existing_eval_id = await conn.fetchval(
            """
            SELECT evaluation_id FROM evaluations 
            WHERE version_id = $1 AND validator_hotkey = $2 AND set_id = $3
        """,
            version_id,
            validator_hotkey,
            set_id,
        )

        if existing_eval_id:
            logger.debug(f"Evaluation already exists for version {version_id}, validator {validator_hotkey}, set {set_id}")
            return str(existing_eval_id)

        # Create new evaluation
        eval_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, 'waiting', NOW())
        """,
            eval_id,
            version_id,
            validator_hotkey,
            set_id,
        )

        return eval_id

    @staticmethod
    async def create_screening_and_send(conn: asyncpg.Connection, agent: 'MinerAgent', screener: 'Screener') -> bool:
        """Create screening evaluation"""
        from api.src.socket.websocket_manager import WebSocketManager
        ws = WebSocketManager.get_instance()

        eval_id = str(uuid.uuid4())
        set_id = await conn.fetchval("SELECT MAX(set_id) from evaluation_sets")

        await conn.execute(
            """
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, 'waiting', NOW())
        """,
            eval_id,
            agent.version_id,
            screener.hotkey,
            set_id,
        )
        
        message = {
            "event": "screen-agent",
            "evaluation_id": eval_id,
            "agent_version": agent.model_dump(mode="json"),
        }
        logger.info(f"Sending screen-agent message to screener {screener.hotkey}: evaluation_id={eval_id}, agent={agent.agent_name}")
        
        return await ws.send_to_client(screener, message)

    @staticmethod
    async def get_by_id(evaluation_id: str) -> Optional["Evaluation"]:
        """Get evaluation by ID"""
        async with get_db_connection() as conn:
            row = await conn.fetchrow("SELECT * FROM evaluations WHERE evaluation_id = $1", evaluation_id)
            if not row:
                return None

            return Evaluation(
                evaluation_id=row["evaluation_id"],
                version_id=row["version_id"],
                validator_hotkey=row["validator_hotkey"],
                set_id=row["set_id"],
                status=EvaluationStatus.from_string(row["status"]),
                score=row.get("score"),
            )

    @staticmethod
    async def screen_next_awaiting_agent(screener: 'Screener'):
        """Atomically claim an awaiting agent for screening and create evaluation"""
        from api.src.backend.entities import MinerAgent, AgentStatus

        logger.info(f"screen_next_awaiting_agent called for screener {screener.hotkey}")
        
        # Atomically check availability and mark busy to prevent race conditions
        if not screener.is_available():
            logger.info(f"Screener {screener.hotkey} is not available (status: {screener.status})")
            return
        screener.status = "claiming_agent"

        async with get_transaction() as conn:
            # First check if there are any agents awaiting screening
            awaiting_count = await conn.fetchval("SELECT COUNT(*) FROM miner_agents WHERE status = 'awaiting_screening'")
            logger.info(f"Found {awaiting_count} agents with awaiting_screening status")
            
            if awaiting_count > 0:
                # Log the agents for debugging
                awaiting_agents = await conn.fetch("SELECT version_id, miner_hotkey, agent_name, created_at FROM miner_agents WHERE status = 'awaiting_screening' ORDER BY created_at ASC")
                for agent in awaiting_agents[:3]:  # Log first 3
                    logger.info(f"Awaiting agent: {agent['agent_name']} ({agent['version_id']}) from {agent['miner_hotkey']}")
            
            # Atomically claim the next awaiting agent
            claimed_agent = await conn.fetchrow(
                """
                UPDATE miner_agents 
                SET status = 'screening' 
                WHERE version_id = (
                    SELECT version_id FROM miner_agents 
                    WHERE status = 'awaiting_screening' 
                    ORDER BY created_at ASC 
                    LIMIT 1
                )
                RETURNING version_id, miner_hotkey, agent_name, version_num, created_at
            """
            )

            if not claimed_agent:
                screener.set_available()  # Revert to available if no agent found
                logger.info(f"No agents claimed by screener {screener.hotkey} despite {awaiting_count} awaiting")
                return

            logger.info(f"Screener {screener.hotkey} claimed agent {claimed_agent['agent_name']} ({claimed_agent['version_id']})")

            agent = MinerAgent(
                version_id=claimed_agent["version_id"],
                miner_hotkey=claimed_agent["miner_hotkey"],
                agent_name=claimed_agent["agent_name"],
                version_num=claimed_agent["version_num"],
                created_at=claimed_agent["created_at"],
                status=AgentStatus.screening,
            )
        
            # Set final status before sending evaluation
            screener.status = f"Assigned agent {agent.agent_name}"
            screener.current_agent_name = agent.agent_name
            
            success = await Evaluation.create_screening_and_send(conn, agent, screener)
            logger.info(f"WebSocket send to screener {screener.hotkey} for agent {agent.agent_name}: {'SUCCESS' if success else 'FAILED'}")

    @staticmethod
    async def check_miner_has_no_running_evaluations(conn: asyncpg.Connection, miner_hotkey: str) -> bool:
        """Check if miner has any running evaluations"""
        has_running = await conn.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM evaluations e 
            JOIN miner_agents ma ON e.version_id = ma.version_id 
            WHERE ma.miner_hotkey = $1 AND e.status = 'running')
        """,
            miner_hotkey,
        )
        return not has_running

    @staticmethod
    async def replace_old_agents(conn: asyncpg.Connection, miner_hotkey: str) -> None:
        """Replace all old agents and their evaluations for a miner"""
        # Replace old agents
        await conn.execute("UPDATE miner_agents SET status = 'replaced' WHERE miner_hotkey = $1 AND status != 'scored'", miner_hotkey)

        # Replace their evaluations
        await conn.execute(
            """
            UPDATE evaluations SET status = 'replaced' 
            WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = $1)
            AND status IN ('waiting', 'running')
        """,
            miner_hotkey,
        )

        # Cancel evaluation_runs for replaced evaluations
        await conn.execute(
            """
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW() 
            WHERE evaluation_id IN (
                SELECT evaluation_id FROM evaluations 
                WHERE version_id IN (SELECT version_id FROM miner_agents WHERE miner_hotkey = $1)
                AND status = 'replaced'
            )
        """,
            miner_hotkey,
        )

    @staticmethod
    async def has_waiting_for_validator(validator: 'Validator') -> bool:
        """Atomically handle validator connection: create missing evaluations and check for work"""
        async with get_transaction() as conn:
            # Get current max set_id
            max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
            
            # Create evaluations for waiting/evaluating agents that don't have one for this validator
            agents = await conn.fetch(
                """
                SELECT version_id FROM miner_agents 
                WHERE status IN ('waiting', 'evaluating') 
                AND NOT EXISTS (
                    SELECT 1 FROM evaluations 
                    WHERE version_id = miner_agents.version_id 
                    AND validator_hotkey = $1 
                    AND set_id = $2
                )
            """,
                validator.hotkey,
                max_set_id,
            )

            for agent in agents:
                await Evaluation.create_for_validator(conn, agent["version_id"], validator.hotkey)

        async with get_transaction() as conn:
            # Check if validator has waiting work
            has_work = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM evaluations WHERE validator_hotkey = $1 AND status = 'waiting')", validator.hotkey
            )

            return has_work

    @staticmethod
    async def handle_validator_disconnection(validator_hotkey: str):
        """Handle validator disconnection: reset running evaluations"""
        async with get_transaction() as conn:
            # Get running evaluations for this validator
            running_evals = await conn.fetch(
                """
                SELECT evaluation_id FROM evaluations 
                WHERE validator_hotkey = $1 AND status = 'running'
            """,
                validator_hotkey,
            )

            # Reset each evaluation back to waiting
            for eval_row in running_evals:
                evaluation = await Evaluation.get_by_id(eval_row["evaluation_id"])
                if evaluation:
                    await evaluation.reset_to_waiting(conn)

    @staticmethod
    async def handle_screener_disconnection(screener_hotkey: str):
        """Atomically handle screener disconnection: error active evaluations and reset agents"""
        async with get_transaction() as conn:
            # Get active screening evaluations
            active_screenings = await conn.fetch(
                """
                SELECT evaluation_id, version_id FROM evaluations 
                WHERE validator_hotkey = $1 AND status IN ('running', 'waiting') AND validator_hotkey LIKE 'i-0%'
            """,
                screener_hotkey,
            )

            for screening_row in active_screenings:
                evaluation = await Evaluation.get_by_id(screening_row["evaluation_id"])
                if evaluation:
                    await evaluation.error(conn, "Disconnected from screener")

    @staticmethod
    async def startup_recovery():
        """Fix broken states from shutdown"""
        async with get_transaction() as conn:
            # Reset agent statuses
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening' WHERE status = 'screening'")
            await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE status = 'evaluating'")

            # Reset running evaluations
            running_evals = await conn.fetch("SELECT evaluation_id FROM evaluations WHERE status = 'running'")
            for eval_row in running_evals:
                evaluation = await Evaluation.get_by_id(eval_row["evaluation_id"])
                if evaluation:
                    if evaluation.is_screening:
                        await evaluation.error(conn, "Disconnected from screener")
                    else:
                        await evaluation.reset_to_waiting(conn)

            # Cancel waiting screenings
            waiting_screenings = await conn.fetch("SELECT evaluation_id FROM evaluations WHERE status = 'waiting' AND validator_hotkey LIKE 'i-0%'")
            for screening_row in waiting_screenings:
                evaluation = await Evaluation.get_by_id(screening_row["evaluation_id"])
                if evaluation:
                    await evaluation.error(conn, "Disconnected from screener")

            # Cancel dangling evaluation runs
            await conn.execute("UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW() WHERE status not in ('result_scored', 'cancelled')")

            logger.info("Application startup recovery completed")
