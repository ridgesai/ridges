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
        
    def run_evaluation(evaluation: NewEvaluationInstruction):
        pass
        
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


    def handle_evaluation_request(self):
        # PLASMA: Need this to get passed to me
        evaluation_request = {
            "evaluation_id": "123",
            "runs": [
                {
                    "run_id": "456",
                    "problem_name": "789"
                }
            ],
            "agent_source_code": "..."
        }



        # Make sure all problems exist
        for run in evaluation_request["runs"]:
            problem_name = run["problem_name"]
            if not self.polyglot_suite.has_problem(problem_name) and not self.swebench_verified_suite.has_problem(problem_name):
                # CXII: Handle this
                pass
            

        # Run them all
        for run in evaluation_request["runs"]:
            run_id = run["run_id"]
            problem_name = run["problem_name"]
            
            # Choose the appropriate suite
            suite = self.polyglot_suite if self.polyglot_suite.has_problem(problem_name) else self.swebench_verified_suite

            # This callback will be invoked when the agent finishes running
            def on_agent_finish(agent_result):
                if agent_result["status"] == "success":
                    # This callback will be invoked when the agent finishes evaluating
                    def on_eval_finish(eval_result):
                        if eval_result["status"] == "success":
                            # CXII: The agent successfully evaluated. Handle it
                            pass
                        else:
                            # CXII: The agent errored while evaluating. Handle it
                            pass


                    
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
                    # CXII: The agent errored while running. Handle it
                    pass
                    
                
            
            # Run the agent
            suite.run_agent_in_sandbox_for_problem(
                self.sandbox_manager,
                run_id,
                problem_name,
                evaluation_request["agent_source_code"],
                on_agent_finish,
                timeout=AGENT_TIMEOUT
            )



    def shutdown_evaluations():
        pass