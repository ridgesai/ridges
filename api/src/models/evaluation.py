from datetime import datetime, timezone
import logging
import uuid
import traceback
from typing import List, Optional, Tuple
import asyncpg
import asyncio

from api.src.backend.entities import EvaluationRun, MinerAgent, MinerAgentScored, SandboxStatus
from api.src.backend.db_manager import get_db_connection, get_transaction
from api.src.backend.entities import EvaluationStatus
from api.src.models.screener import Screener
from api.src.models.validator import Validator
from api.src.utils.config import SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD
from api.src.utils.config import PRUNE_THRESHOLD
from api.src.utils.slack import send_slack_message

logger = logging.getLogger(__name__)


class Evaluation:
    """Evaluation model - handles its own lifecycle atomically"""

    _lock = asyncio.Lock()

    def __init__(
        self,
        evaluation_id: str,
        version_id: str,
        validator_hotkey: str,
        set_id: int,
        status: EvaluationStatus,
        terminated_reason: Optional[str] = None,
        score: Optional[float] = None,
        screener_score: Optional[float] = None,
        created_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ):
        self.evaluation_id = evaluation_id
        self.version_id = version_id
        self.validator_hotkey = validator_hotkey
        self.set_id = set_id
        self.status = status
        self.terminated_reason = terminated_reason
        self.created_at = created_at
        self.started_at = started_at
        self.finished_at = finished_at
        self.score = score
        self.screener_score = screener_score
    @property
    def is_screening(self) -> bool:
        return self.screener_stage is not None
    
    @property
    def screener_stage(self) -> Optional[int]:
        return Screener.get_stage(self.validator_hotkey)

    async def start(self, conn: asyncpg.Connection) -> List[EvaluationRun]:
        """Start evaluation"""
        await conn.execute("UPDATE evaluations SET status = 'running', started_at = NOW() WHERE evaluation_id = $1", self.evaluation_id)
        self.status = EvaluationStatus.running

        match self.screener_stage:
            case 1:
                type = "screener-1"
            case 2:
                type = "screener-2"
            case _:
                type = "validator"
        max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
        swebench_instance_ids_data = await conn.fetch(
            "SELECT swebench_instance_id FROM evaluation_sets WHERE set_id = $1 AND type = $2", max_set_id, type
        )
        swebench_instance_ids = [row["swebench_instance_id"] for row in swebench_instance_ids_data]
        evaluation_runs = [
            EvaluationRun(
                run_id=uuid.uuid4(),
                evaluation_id=self.evaluation_id,
                swebench_instance_id=swebench_instance_id,
                response=None,
                error=None,
                pass_to_fail_success=None,
                fail_to_pass_success=None,
                pass_to_pass_success=None,
                fail_to_fail_success=None,
                solved=None,
                status=SandboxStatus.started,
                started_at=datetime.now(timezone.utc),
                sandbox_created_at=None,
                patch_generated_at=None,
                eval_started_at=None,
                result_scored_at=None,
                cancelled_at=None,
            )
            for swebench_instance_id in swebench_instance_ids
        ]
        await conn.executemany(
            "INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, status, started_at) VALUES ($1, $2, $3, $4, $5)",
            [(run.run_id, run.evaluation_id, run.swebench_instance_id, run.status.value, run.started_at) for run in evaluation_runs],
        )

        await self._update_agent_status(conn)
        return evaluation_runs

    async def finish(self, conn: asyncpg.Connection):
        """Finish evaluation, but retry if >=50% of inferences failed and any run errored"""
        
        # DEBUG: Check if this evaluation already has a terminated_reason set
        current_terminated_reason = await conn.fetchval(
            "SELECT terminated_reason FROM evaluations WHERE evaluation_id = $1", 
            self.evaluation_id
        )
        if current_terminated_reason:
            current_status = await conn.fetchval(
                "SELECT status FROM evaluations WHERE evaluation_id = $1", 
                self.evaluation_id
            )
            
            # Print very noticeable debug information
            print("=" * 80)
            print("🚨 CRITICAL DEBUG: finish() called on evaluation with existing terminated_reason! 🚨")
            print("=" * 80)
            print(f"Evaluation ID: {self.evaluation_id}")
            print(f"Version ID: {self.version_id}")
            print(f"Validator Hotkey: {self.validator_hotkey}")
            print(f"Current Status: {current_status}")
            print(f"Existing terminated_reason: {current_terminated_reason}")
            print(f"Is Screening: {self.is_screening}")
            if self.is_screening:
                print(f"Screener Stage: {self.screener_stage}")
            print(f"Current Time: {datetime.now().isoformat()}")
            print()
            print("CALL STACK TRACE:")
            print("-" * 40)
            traceback.print_stack()
            print("=" * 80)
            
            # Also log it for persistent record
            logger.error(
                f"CRITICAL: finish() called on evaluation {self.evaluation_id} "
                f"(version_id: {self.version_id}, validator: {self.validator_hotkey}) "
                f"that already has terminated_reason: '{current_terminated_reason}'. "
                f"Current status: {current_status}. This will result in inconsistent state!"
            )
        
        # Check if we should retry due to inference failures
        successful, total, success_rate, any_run_errored = await self._check_inference_success_rate(conn)
        
        # If we have inferences and >=50% failed AND any run errored, retry instead of finishing
        if total > 0 and success_rate < 0.5 and any_run_errored:
            logger.info(f"Evaluation {self.evaluation_id} completed but {successful}/{total} successful inferences ({success_rate:.1%}) with run errors. Retrying...")
            await self.reset_to_waiting(conn)
            return
        
        await conn.execute("UPDATE evaluations SET status = 'completed', finished_at = NOW() WHERE evaluation_id = $1", self.evaluation_id)
        self.status = EvaluationStatus.completed
        self.score = await conn.fetchval("SELECT score FROM evaluations WHERE evaluation_id = $1", self.evaluation_id)

        # Store validators to notify after agent status update
        validators_to_notify = []
        stage2_screener_to_notify = None

        # If it's a screener, handle stage-specific logic
        if self.is_screening:
            stage = self.screener_stage
            threshold = SCREENING_1_THRESHOLD if stage == 1 else SCREENING_2_THRESHOLD
            if self.score < threshold:
                logger.info(f"Stage {stage} screening failed for agent {self.version_id} with score {self.score} (threshold: {threshold})")
            else:
                logger.info(f"Stage {stage} screening passed for agent {self.version_id} with score {self.score} (threshold: {threshold})")
                
                if stage == 1:
                    # Stage 1 passed -> find ONE available stage 2 screener
                    from api.src.socket.websocket_manager import WebSocketManager
                    ws_manager = WebSocketManager.get_instance()
                    for client in ws_manager.clients.values():
                        if client.get_type() != "screener":
                            continue
                        screener: Screener = client
                        
                        if screener.stage == 2 and screener.is_available():
                            stage2_screener_to_notify = screener
                            break
                elif stage == 2:
                    # Stage 2 passed -> check if we should prune immediately
                    combined_screener_score, score_error = await Screener.get_combined_screener_score(conn, self.version_id)
                    # ^ if this is None, we should likely not be here, because it means that either there was no screener 1 or screener 2 evaluation, but in which case how would be here anyway?
                    if score_error:
                        await send_slack_message(f"Stage 2 screener score error for version {self.version_id}: {score_error}")
                    top_agent = await MinerAgentScored.get_top_agent(conn)
                    
                    if top_agent and combined_screener_score is not None and (top_agent.avg_score - combined_screener_score) > PRUNE_THRESHOLD:
                        # Score is too low, prune miner agent and don't create evaluations
                        await conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", self.version_id)
                        logger.info(f"Pruned agent {self.version_id} immediately after screener-2 with combined score {combined_screener_score:.3f} (threshold: {top_agent.avg_score - PRUNE_THRESHOLD:.3f})")
                        return {
                            "stage2_screener": None,
                            "validators": []
                        }
                    
                    # Score is acceptable -> notify validators
                    from api.src.models.validator import Validator

                    # Create evaluation records but don't notify yet
                    import random
                    all_validators = await Validator.get_connected()
                    validators_to_notify = random.sample(all_validators, min(2, len(all_validators)))
                    for validator in validators_to_notify:
                        if (combined_screener_score is None):
                            await send_slack_message(f"111 Screener score is None when creating evaluation for validator {validator.hotkey}, version {self.version_id}")
                            await send_slack_message(f"Evaluation object: {str(self)}")
                        await self.create_for_validator(conn, self.version_id, validator.hotkey, combined_screener_score)
                    
                    # Prune low-scoring evaluations after creating validator evaluations
                    await Evaluation.prune_low_waiting(conn)

        await self._update_agent_status(conn)

        # Return notification targets to be handled OUTSIDE this transaction  
        return {
            "stage2_screener": stage2_screener_to_notify,
            "validators": validators_to_notify
        }

    async def _check_inference_success_rate(self, conn: asyncpg.Connection) -> Tuple[int, int, float, bool]:
        """Check inference success rate for this evaluation
        
        Returns:
            tuple: (successful_count, total_count, success_rate, any_run_errored)
        """
        result = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_inferences,
                COUNT(*) FILTER (WHERE status_code = 200) as successful_inferences,
                COUNT(*) FILTER (WHERE er.error IS NOT NULL) > 0 as any_run_errored
            FROM inferences i
            JOIN evaluation_runs er ON i.run_id = er.run_id
            WHERE er.evaluation_id = $1 AND er.status != 'cancelled'
        """, self.evaluation_id)
        
        total = result['total_inferences'] or 0
        successful = result['successful_inferences'] or 0
        success_rate = successful / total if total > 0 else 1.0
        any_run_errored = bool(result['any_run_errored'])
        
        return successful, total, success_rate, any_run_errored

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
        await conn.execute("UPDATE evaluation_runs SET status = 'cancelled' WHERE evaluation_id = $1", self.evaluation_id)

        await self._update_agent_status(conn)

    async def _update_agent_status(self, conn: asyncpg.Connection):
        """Update agent status based on evaluation state - handles multi-stage screening"""
        
        # Handle screening completion
        if self.is_screening and self.status == EvaluationStatus.completed:
            stage = self.screener_stage
            threshold = SCREENING_1_THRESHOLD if stage == 1 else SCREENING_2_THRESHOLD
            if self.score is not None and self.score >= threshold:
                if stage == 1:
                    # Stage 1 passed -> move to stage 2
                    await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_2' WHERE version_id = $1", self.version_id)
                elif stage == 2:
                    # Stage 2 passed -> ready for validation
                    await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE version_id = $1", self.version_id)
            else:
                if stage == 1:
                    # Stage 1 failed
                    await conn.execute("UPDATE miner_agents SET status = 'failed_screening_1' WHERE version_id = $1", self.version_id)
                elif stage == 2:
                    # Stage 2 failed
                    await conn.execute("UPDATE miner_agents SET status = 'failed_screening_2' WHERE version_id = $1", self.version_id)
            return

        # Handle screening errors like disconnection - reset to appropriate awaiting state
        if self.is_screening and self.status == EvaluationStatus.error:
            stage = self.screener_stage
            if stage == 1:
                await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_1' WHERE version_id = $1", self.version_id)
            elif stage == 2:
                await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_2' WHERE version_id = $1", self.version_id)
            return

        # Check for any stage 1 screening evaluations (only running - waiting evaluations don't mean agent is actively being screened)
        stage1_count = await conn.fetchval(
            """SELECT COUNT(*) FROM evaluations WHERE version_id = $1 
               AND (validator_hotkey LIKE 'screener-1-%' OR validator_hotkey LIKE 'i-0%') 
               AND status = 'running'""",
            self.version_id,
        )
        if stage1_count > 0:
            await conn.execute("UPDATE miner_agents SET status = 'screening_1' WHERE version_id = $1", self.version_id)
            return

        # Check for any stage 2 screening evaluations (only running - waiting evaluations don't mean agent is actively being screened)
        stage2_count = await conn.fetchval(
            """SELECT COUNT(*) FROM evaluations WHERE version_id = $1 
               AND validator_hotkey LIKE 'screener-2-%' 
               AND status = 'running'""",
            self.version_id,
        )
        if stage2_count > 0:
            await conn.execute("UPDATE miner_agents SET status = 'screening_2' WHERE version_id = $1", self.version_id)
            return

        # Handle evaluation status transitions for regular evaluations
        if self.status == EvaluationStatus.running and not self.is_screening:
            await conn.execute("UPDATE miner_agents SET status = 'evaluating' WHERE version_id = $1", self.version_id)
            return

        # For other cases, check remaining regular evaluations (non-screening)
        waiting_count = await conn.fetchval(
            """SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status = 'waiting' 
               AND validator_hotkey NOT LIKE 'screener-%' 
               AND validator_hotkey NOT LIKE 'i-0%'""", 
            self.version_id
        )
        running_count = await conn.fetchval(
            """SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status = 'running' 
               AND validator_hotkey NOT LIKE 'screener-%' 
               AND validator_hotkey NOT LIKE 'i-0%'""", 
            self.version_id
        )
        completed_count = await conn.fetchval(
            """SELECT COUNT(*) FROM evaluations WHERE version_id = $1 AND status IN ('completed', 'pruned') 
               AND validator_hotkey NOT LIKE 'screener-%' 
               AND validator_hotkey NOT LIKE 'i-0%'""", 
            self.version_id
        )

        if waiting_count > 0 and running_count == 0:
            await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE version_id = $1", self.version_id)
        elif waiting_count == 0 and running_count == 0 and completed_count > 0:
            # Calculate and update innovation score for this agent before setting status to 'scored'
            await self._update_innovation_score(conn)
            await conn.execute("UPDATE miner_agents SET status = 'scored' WHERE version_id = $1", self.version_id)
        else:
            await conn.execute("UPDATE miner_agents SET status = 'evaluating' WHERE version_id = $1", self.version_id)

    async def _update_innovation_score(self, conn: asyncpg.Connection):
        """Calculate and update innovation score for this evaluation's agent in one atomic query"""
        try:
            # Single atomic query that calculates and updates innovation score
            updated_rows = await conn.execute("""
                WITH agent_runs AS (
                    -- Get all result_scored runs for this agent
                    SELECT
                        r.swebench_instance_id,
                        r.solved,
                        r.started_at,
                        r.run_id
                    FROM evaluation_runs r
                    JOIN evaluations e ON e.evaluation_id = r.evaluation_id
                    WHERE e.version_id = $1
                      AND r.status = 'result_scored'
                ),
                runs_with_prior AS (
                    -- Calculate prior solved ratio for each run using window functions
                    SELECT
                        swebench_instance_id,
                        solved,
                        started_at,
                        run_id,
                        -- Calculate average solve rate for this instance before this run
                        COALESCE(
                            AVG(CASE WHEN solved THEN 1.0 ELSE 0.0 END) 
                            OVER (
                                PARTITION BY swebench_instance_id 
                                ORDER BY started_at 
                                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                            ), 0.0
                        ) AS prior_solved_ratio
                    FROM agent_runs
                ),
                innovation_calculation AS (
                    SELECT
                        COALESCE(
                            AVG((CASE WHEN solved THEN 1.0 ELSE 0.0 END) - prior_solved_ratio), 0.0
                        ) AS innovation_score
                    FROM runs_with_prior
                )
                UPDATE miner_agents 
                SET innovation = (SELECT innovation_score FROM innovation_calculation)
                WHERE version_id = $1
            """, self.version_id)
            
            logger.info(f"Updated innovation score for agent {self.version_id} (affected {updated_rows} rows)")
            
        except Exception as e:
            logger.error(f"Failed to calculate innovation score for agent {self.version_id}: {e}")
            # Set innovation score to NULL on error to indicate calculation failure
            await conn.execute(
                "UPDATE miner_agents SET innovation = NULL WHERE version_id = $1",
                self.version_id
            )

    @staticmethod
    def get_lock():
        """Get the shared lock for evaluation operations"""
        return Evaluation._lock
    
    @staticmethod
    def assert_lock_held():
        """Debug assertion to ensure lock is held for critical operations"""
        if not Evaluation._lock.locked():
            raise AssertionError("Evaluation lock must be held for this operation")

    @staticmethod
    async def create_for_validator(conn: asyncpg.Connection, version_id: str, validator_hotkey: str, screener_score: Optional[float] = None) -> Optional[str]:
        """Create evaluation for validator"""

        # We should always have a screener score when creating an evaluation for a validator
        if screener_score is None:
            combined_screener_score, score_error = await Screener.get_combined_screener_score(conn, version_id)
            await send_slack_message(f"333 Screener score is None when creating evaluation for validator {validator_hotkey}, version {version_id}")
            if score_error:
                await send_slack_message(f"Error calculating screener score: {score_error}")
            else:
                await send_slack_message(f"If the combined screener score was calculated right now, it would be {combined_screener_score}")

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
            if screener_score is None:
                await send_slack_message(f"444 Screener score is None when creating evaluation for validator {validator_hotkey}, version {version_id}, and the evaluation already exists {existing_eval_id}")
            return str(existing_eval_id)


        if screener_score is None:
            await send_slack_message(f"555 Screener score is None when creating evaluation for validator {validator_hotkey}, version {version_id}. AS SUCH, DID NOT CREATE THE EVALUATION!")
            await send_slack_message("========================================================")
            return None
        else:
            # Create new evaluation
            eval_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, screener_score)
                VALUES ($1, $2, $3, $4, 'waiting', NOW(), $5)
            """,
                eval_id,
                version_id,
                validator_hotkey,
                set_id,
                screener_score,
            )
            return eval_id

    @staticmethod
    async def create_screening_and_send(conn: asyncpg.Connection, agent: 'MinerAgent', screener: 'Screener') -> Tuple[str, bool]:
        """Create screening evaluation"""
        from api.src.socket.websocket_manager import WebSocketManager

        # Safety check: Ensure screener doesn't already have a running evaluation
        existing_evaluation = await conn.fetchrow(
            """
            SELECT evaluation_id, status FROM evaluations 
            WHERE validator_hotkey = $1 AND status = 'running'
            LIMIT 1
            """,
            screener.hotkey
        )
        
        if existing_evaluation:
            logger.error(f"CRITICAL: Screener {screener.hotkey} already has running evaluation {existing_evaluation['evaluation_id']} - refusing to create duplicate screening")
            return "", False

        ws = WebSocketManager.get_instance()

        set_id = await conn.fetchval("SELECT MAX(set_id) from evaluation_sets")

        eval_id = str(uuid.uuid4())

        evaluation_data = await conn.fetchrow(
            """
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, 'waiting', NOW())
            RETURNING *
        """,
            eval_id,
            agent.version_id,
            screener.hotkey,
            set_id,
        )

        evaluation = Evaluation(**evaluation_data)

        logger.info(f"Evaluation to send to screener {screener.hotkey}: {evaluation.evaluation_id}")
        evaluation_runs = await evaluation.start(conn)

        message = {
            "event": "screen-agent",
            "evaluation_id": str(eval_id),
            "agent_version": agent.model_dump(mode="json"),
            "evaluation_runs": [run.model_dump(mode="json") for run in evaluation_runs],
        }
        logger.info(f"Sending screen-agent message to screener {screener.hotkey}: evaluation_id={eval_id}, agent={agent.agent_name}")
        
        await ws.send_to_all_non_validators("evaluation-started", message)
        return eval_id, await ws.send_to_client(screener, message)

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
    async def screen_next_awaiting_agent(screener: "Screener"):
        """Atomically claim an awaiting agent for screening - MUST be called within lock"""
        from api.src.backend.entities import MinerAgent, AgentStatus
        
        Evaluation.assert_lock_held()
        logger.info(f"screen_next_awaiting_agent called for screener {screener.hotkey} (stage {screener.stage})")

        # Check availability (could be in "reserving" state from upload)
        if screener.status not in ["available", "reserving"]:
            logger.info(f"Screener {screener.hotkey} not available (status: {screener.status})")
            return
        
        # Determine which status to look for based on screener stage
        target_status = f"awaiting_screening_{screener.stage}"
        target_screening_status = f"screening_{screener.stage}"
        
        async with get_transaction() as conn:
            # First check if there are any agents awaiting this stage of screening
            awaiting_count = await conn.fetchval("SELECT COUNT(*) FROM miner_agents WHERE status = $1", target_status)
            logger.info(f"Found {awaiting_count} agents with {target_status} status")

            if awaiting_count > 0:
                # Log the agents for debugging
                awaiting_agents = await conn.fetch(
                    """
                    SELECT version_id, miner_hotkey, agent_name, created_at FROM miner_agents 
                    WHERE status = $1 
                    AND miner_hotkey NOT IN (SELECT miner_hotkey from banned_hotkeys)
                    ORDER BY created_at ASC
                    """,
                    target_status
                )
                for agent in awaiting_agents[:3]:  # Log first 3
                    logger.info(f"Awaiting stage {screener.stage} agent: {agent['agent_name']} ({agent['version_id']}) from {agent['miner_hotkey']}")

            # Atomically claim the next awaiting agent for this stage using CTE with FOR UPDATE SKIP LOCKED
            logger.debug(f"Stage {screener.stage} screener {screener.hotkey} attempting to claim agent with status '{target_status}'")
            try:
                claimed_agent = await conn.fetchrow(
                    """
                    WITH next_agent AS (
                        SELECT version_id FROM miner_agents 
                        WHERE status = $1 
                        AND miner_hotkey NOT IN (SELECT miner_hotkey from banned_hotkeys)
                        ORDER BY created_at ASC 
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE miner_agents 
                    SET status = $2
                    FROM next_agent
                    WHERE miner_agents.version_id = next_agent.version_id
                    RETURNING miner_agents.version_id, miner_hotkey, agent_name, version_num, created_at
                """,
                    target_status,
                    target_screening_status
                )
            except Exception as e:
                logger.warning(f"Database error while claiming agent for screener {screener.hotkey}: {e}")
                claimed_agent = None

            if not claimed_agent:
                screener.set_available()  # Ensure available state is set
                logger.info(f"No stage {screener.stage} agents claimed by screener {screener.hotkey} despite {awaiting_count} awaiting")
                return

            logger.info(f"Stage {screener.stage} screener {screener.hotkey} claimed agent {claimed_agent['agent_name']} ({claimed_agent['version_id']})")

            agent = MinerAgent(
                version_id=claimed_agent["version_id"],
                miner_hotkey=claimed_agent["miner_hotkey"],
                agent_name=claimed_agent["agent_name"],
                version_num=claimed_agent["version_num"],
                created_at=claimed_agent["created_at"],
                status=target_screening_status,  # Already set to correct status in query
            )
        
            eval_id, success = await Evaluation.create_screening_and_send(conn, agent, screener)

            if success:
                # Commit screener state changes
                screener.status = "screening"
                screener.current_agent_name = agent.agent_name
                screener.current_evaluation_id = eval_id
                screener.current_agent_hotkey = agent.miner_hotkey
                logger.info(f"Stage {screener.stage} screener {screener.hotkey} successfully assigned to {agent.agent_name}")
                
                # Broadcast status change to dashboard
                screener._broadcast_status_change()
            else:
                # Reset screener on failure
                screener.set_available()
                logger.warning(f"Failed to send work to stage {screener.stage} screener {screener.hotkey}")

    @staticmethod
    async def get_progress(evaluation_id: str) -> float:
        """Get progress of evaluation across all runs"""
        async with get_db_connection() as conn:
            progress = await conn.fetchval("""
                SELECT COALESCE(AVG(
                    CASE status
                        WHEN 'started' THEN 0.2
                        WHEN 'sandbox_created' THEN 0.4
                        WHEN 'patch_generated' THEN 0.6
                        WHEN 'eval_started' THEN 0.8
                        WHEN 'result_scored' THEN 1.0
                        ELSE 0.0
                    END
                ), 0.0)
                FROM evaluation_runs 
                WHERE evaluation_id = $1
                AND status NOT IN ('cancelled', 'error')
            """, evaluation_id)
            return float(progress)

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
    async def has_waiting_for_validator(validator: "Validator") -> bool:
        """Atomically handle validator connection: create missing evaluations and check for work"""
        async with get_transaction() as conn:
            # Get current max set_id
            max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")

            # Create evaluations for waiting/evaluating agents that don't have one for this validator
            agents = await conn.fetch(
                """
                SELECT * FROM miner_agents 
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
                combined_screener_score, score_error = await Screener.get_combined_screener_score(conn, agent["version_id"])
                if (combined_screener_score is None):
                    await send_slack_message(f"222 Agent object: {dict(agent)}")
                    await send_slack_message(f"222 Screener score is None when creating evaluation for validator {validator.hotkey}, version {agent['version_id']}")
                    if score_error:
                        await send_slack_message(f"222 Error: {score_error}")
                await Evaluation.create_for_validator(conn, agent["version_id"], validator.hotkey, combined_screener_score)

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
            # Get active screening evaluations for all screener types
            active_screenings = await conn.fetch(
                """
                SELECT evaluation_id, version_id FROM evaluations 
                WHERE validator_hotkey = $1 AND status IN ('running', 'waiting') 
                AND (validator_hotkey LIKE 'screener-%' OR validator_hotkey LIKE 'i-0%')
            """,
                screener_hotkey,
            )

            for screening_row in active_screenings:
                evaluation = await Evaluation.get_by_id(screening_row["evaluation_id"])
                if evaluation:
                    await evaluation.error(conn, "Disconnected from screener (error code 1)")

    @staticmethod
    async def startup_recovery():
        """Fix broken states from shutdown - handles multi-stage screening"""
        async with get_transaction() as conn:
            # Reset agent statuses for multi-stage screening
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_1' WHERE status = 'screening_1'")
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_2' WHERE status = 'screening_2'")
            await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE status = 'evaluating'")
            
            # Legacy status recovery for backward compatibility
            await conn.execute("UPDATE miner_agents SET status = 'awaiting_screening_1' WHERE status = 'screening'")
            await conn.execute("UPDATE miner_agents SET status = 'waiting' WHERE status = 'evaluation'")  # Legacy alias

            # Reset running evaluations
            running_evals = await conn.fetch("SELECT evaluation_id FROM evaluations WHERE status = 'running'")
            for eval_row in running_evals:
                evaluation = await Evaluation.get_by_id(eval_row["evaluation_id"])
                if evaluation:
                    if evaluation.is_screening:
                        await evaluation.error(conn, "Disconnected from screener (error code 2)")
                    else:
                        await evaluation.reset_to_waiting(conn)

            # Check for running evaluations that should be auto-completed
            stuck_evaluations = await conn.fetch(
                """
                SELECT e.evaluation_id FROM evaluations e
                WHERE e.status = 'running'
                AND NOT EXISTS (
                    SELECT 1 FROM evaluation_runs er 
                    WHERE er.evaluation_id = e.evaluation_id 
                    AND er.status NOT IN ('result_scored', 'cancelled')
                )
                AND EXISTS (
                    SELECT 1 FROM evaluation_runs er2
                    WHERE er2.evaluation_id = e.evaluation_id
                )
                """
            )

            for stuck_eval in stuck_evaluations:
                evaluation = await Evaluation.get_by_id(stuck_eval["evaluation_id"])
                if evaluation:
                    logger.info(f"Auto-completing stuck evaluation {evaluation.evaluation_id} during startup recovery")
                    # During startup recovery, don't trigger notifications
                    _ = await evaluation.finish(conn)

            # Cancel waiting screenings for all screener types
            waiting_screenings = await conn.fetch(
                """SELECT evaluation_id FROM evaluations WHERE status = 'waiting' 
                   AND (validator_hotkey LIKE 'screener-%' OR validator_hotkey LIKE 'i-0%')"""
            )
            for screening_row in waiting_screenings:
                evaluation = await Evaluation.get_by_id(screening_row["evaluation_id"])
                if evaluation:
                    await evaluation.error(conn, "Disconnected from screener (error code 3)")

            # Cancel dangling evaluation runs
            await conn.execute(
                "UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW() WHERE status not in ('result_scored', 'cancelled')"
            )

            # Prune low-scoring evaluations that should not continue waiting
            await Evaluation.prune_low_waiting(conn)

            logger.info("Application startup recovery completed with multi-stage screening support")

    @staticmethod
    async def prune_low_waiting(conn: asyncpg.Connection):
        """Prune evaluations that aren't close enough to the top agent final validation score"""
        # Get the top agent final validation score for the current set
        top_agent = await MinerAgentScored.get_top_agent(conn)
        
        if not top_agent:
            logger.info("No completed evaluations with final validation scores found for pruning")
            return
        
        # Calculate the threshold (configurable lower-than-top final validation score)
        threshold = top_agent.avg_score - PRUNE_THRESHOLD
        
        # Get current set_id for the query
        max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
        
        # Find evaluations with low screener scores that should be pruned
        # We prune based on screener_score being below screening thresholds
        low_score_evaluations = await conn.fetch("""
            SELECT e.evaluation_id, e.version_id, e.validator_hotkey, e.screener_score
            FROM evaluations e
            JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE e.set_id = $1 
            AND e.status = 'waiting'
            AND e.screener_score IS NOT NULL
            AND e.screener_score < $2
            AND ma.status NOT IN ('pruned', 'replaced')
        """, max_set_id, threshold)
        
        if not low_score_evaluations:
            logger.info(f"No evaluations found with screener_score below threshold {threshold}")
            return
        
        # Get unique version_ids to prune
        version_ids_to_prune = list(set(eval['version_id'] for eval in low_score_evaluations))
        
        # Update evaluations to pruned status
        await conn.execute("""
            UPDATE evaluations 
            SET status = 'pruned', finished_at = NOW() 
            WHERE evaluation_id = ANY($1)
        """, [eval['evaluation_id'] for eval in low_score_evaluations])
        
        # Update miner_agents to pruned status
        await conn.execute("""
            UPDATE miner_agents 
            SET status = 'pruned' 
            WHERE version_id = ANY($1)
        """, version_ids_to_prune)
        
        logger.info(f"Pruned {len(low_score_evaluations)} evaluations and {len(version_ids_to_prune)} agents with screener_score below threshold {threshold}")

