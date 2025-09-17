from new_validator.resource_management import get_system_metrics
from new_validator.connection import ConnectionManager

import asyncio
from typing import Optional

from logging import getLogger
from pathlib import Path
from shared.messaging import Heartbeat, NewEvaluationInstruction

from new_validator.sandbox import SandboxManager
from new_validator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from new_validator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite

import new_validator.config as CONFIG
from new_validator.config import AGENT_TIMEOUT, EVAL_TIMEOUT

logger = getLogger(__name__)

class EvaluationManager():
    connection_manager: ConnectionManager
    evaluation_task: Optional[asyncio.Task]
    heartbeat_task: Optional[asyncio.Task]

    sandbox_manager: SandboxManager
    polyglot_suite: PolyglotSuite
    swebench_verified_suite: SWEBenchVerifiedSuite



    def __init__(self, connection_manager: ConnectionManager) -> None:
        self.connection_manager = connection_manager

        # Start heartbeat task
        self.heartbeat_task = asyncio.create_task(self._send_heartbeat())

        self.sandbox_manager = SandboxManager()
        self.polyglot_suite = PolyglotSuite(Path(__file__) / "datasets" / "polyglot")
        self.swebench_verified_suite = SWEBenchVerifiedSuite(Path(__file__) / "dataset" / "swebench_verified")

        # We should somehow fetch the list of SWE-Bench Verified problems
        # And then prebuild the images to speed up evals
        # TODO: Define SWEBENCH_VERIFIED_PROBLEMS or fetch from suite
        # self.swebench_verified_suite.prebuild_problem_images(self.sandbox_manager, SWEBENCH_VERIFIED_PROBLEMS)

    def run_evaluation(self, evaluation: NewEvaluationInstruction):
        """Run evaluation using the new agent runner library."""
        
        logger.info(f"Starting evaluation {evaluation.evaluation_id} for agent {evaluation.agent_version.agent_name}")
        
        # Make sure all problems exist
        for run in evaluation.evaluation_runs:
            # Use swebench_instance_id as the problem identifier
            # This is just to make it work for now, realistically we should call this "problem_name" or something
            problem_name = run.swebench_instance_id
            if not self.polyglot_suite.has_problem(problem_name) and not self.swebench_verified_suite.has_problem(problem_name):
                # CXII: Handle this
                # Need to send error back to platform
                return
            

        # Run them all
        for run in evaluation.evaluation_runs:
            # Same idea as above
            run_id = str(run.run_id)  # Convert UUID to string
            problem_name = run.swebench_instance_id
            
            logger.info(f"Running evaluation for problem {problem_name} with run_id {run_id}")
            
            # Choose the appropriate suite
            suite = self.polyglot_suite if self.polyglot_suite.has_problem(problem_name) else self.swebench_verified_suite

            # This callback will be invoked when the agent finishes running
            def on_agent_finish(agent_result):
                if agent_result["status"] == "success":
                    logger.info(f"Agent finished successfully for run {run_id}")
                    
                    # This callback will be invoked when the agent finishes evaluating
                    def on_eval_finish(eval_result):
                        if eval_result["status"] == "success":
                            logger.info(f"Evaluation completed successfully for run {run_id}")
                            # TODO: Send evaluation result back to platform
                        else:
                            logger.error(f"Evaluation failed for run {run_id}: {eval_result.get('error', 'Unknown error')}")
                            # TODO: Send error result back to platform

                    # Evaluate the agent
                    suite.evaluate_solution_diff(
                        self.sandbox_manager,
                        run_id,
                        problem_name,
                        agent_result["diff"],
                        on_eval_finish,
                        timeout=EVAL_TIMEOUT
                    )
                else:
                    logger.error(f"Agent failed for run {run_id}: {agent_result.get('error', 'Unknown error')}")
                    # TODO: Send agent error back to platform
                    
            # Run the agent - we need to get the agent source code from somewhere
            # TODO: The agent source code should be available in the MinerAgent object or fetched separately
            agent_source_code = "# TODO: Get agent source code from evaluation.agent_version"
            
            suite.run_agent_in_sandbox_for_problem(
                self.sandbox_manager,
                run_id,
                problem_name,
                agent_source_code,
                on_agent_finish,
                timeout=AGENT_TIMEOUT
            )
        
    async def _send_heartbeat(self):
        """Send periodic heartbeat messages with system metrics to the platform."""
        while self.connection_manager.ws:
            await asyncio.sleep(2.5)

            status = "available"

            if self.evaluation_task is not None and not self.evaluation_task.done() and not self.evaluation_task.cancelled():
                status = "evaluating"

            # Collect system metrics
            try:
                logger.debug("Collxecting system metrics...")
                system_metrics = await get_system_metrics()
                logger.debug(f"Raw system metrics collected: {system_metrics}")
                
                # Only include metrics that aren't None
                metrics_to_send = {k: v for k, v in system_metrics.items() if v is not None}
                logger.debug(f"Non-null metrics to send: {metrics_to_send}")
                
                heartbeat = Heartbeat(
                    status=status,
                    **metrics_to_send 
                )

                if metrics_to_send:
                    logger.info(f"📊 Sending heartbeat WITH metrics: {metrics_to_send}")
                else:
                    logger.warning("📊 Sending heartbeat WITHOUT metrics (all None or psutil unavailable)")

                await self.connection_manager.send(message=heartbeat)

            except Exception as e:
                logger.error(f"❌ Failed to collect system metrics, sending heartbeat without them: {e}")
                # Fallback to heartbeat without metrics
                await self.send({"event": "heartbeat", "status": status})



    def shutdown_evaluations(self):
        # CXII: Check when this is called because we automatically shutdown evaluations anyway
        # Do you want this to send a message to the platform?
        self.sandbox_manager.cleanup_all()
        pass