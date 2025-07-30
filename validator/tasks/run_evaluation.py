"""Task for running agents in sandboxes."""

import asyncio
from typing import TYPE_CHECKING, List
from ddtrace import tracer

import httpx
from swebench.harness.utils import load_swebench_dataset

from validator.config import RIDGES_API_URL
from validator.sandbox.manager import SandboxManager
from validator.sandbox.schema import AgentVersion, EvaluationRun, SwebenchProblem
from validator.sandbox.constants import AGENTS_BASE_DIR
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)


@tracer.wrap(resource="run-evaluation")
async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, agent_version: AgentVersion, evaluation_runs: List[EvaluationRun]):
    """Run evaluation for a specific agent version"""
    logger.info(f"Starting evaluation {evaluation_id} for agent {agent_version.miner_hotkey}")

    sandbox_manager = SandboxManager(websocket_app)
    websocket_app.sandbox_manager = sandbox_manager  # Store reference for cancellation
    errored = False

    try:
        # Download agent code
        agent_dir = AGENTS_BASE_DIR / agent_version.miner_hotkey / str(agent_version.version_num)
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "agent.py"
        async with httpx.AsyncClient(timeout=300) as client:
            logger.info(f"Downloading agent code for version {agent_version.version_id}")
            response = await client.get(
                f"{RIDGES_API_URL}/retrieval/agent-version-file",
                params={"version_id": agent_version.version_id},
            )
            response.raise_for_status()
            agent_file.write_bytes(response.content)

        
        # Get problems for the evaluation runs
        instance_ids = [evaluation_run.swebench_instance_id for evaluation_run in evaluation_runs]
        instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", instance_ids)
        problems = {
            instance["instance_id"]: SwebenchProblem(
                instance_id=instance["instance_id"],
                problem_statement=instance["problem_statement"],
                repo=instance["repo"],
                base_commit=instance["base_commit"],
            )
            for instance in instances
        }

        for evaluation_run in evaluation_runs:
            problem = problems[evaluation_run.swebench_instance_id]
            await sandbox_manager.create_sandbox(evaluation_run, problem, agent_dir)

        await sandbox_manager.run_all_sandboxes()
        logger.info(f"Evaluation {evaluation_id} completed successfully")
    except asyncio.CancelledError:
        logger.info(f"Evaluation {evaluation_id} was cancelled")
        errored = True
        raise  # Re-raise CancelledError so it can be handled by the websocket app
    except Exception as e:
        logger.error(f"Error during evaluation: {e}", exc_info=True)
        errored = True
    finally:
        sandbox_manager.cleanup(force_cancel=errored)
