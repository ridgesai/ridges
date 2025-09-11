"""Task for running agents in sandboxes."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, List
from ddtrace import tracer
from validator.utils.http_client import get_shared_client

from validator.config import RIDGES_API_URL
from validator.sandbox.manager import SandboxManager
from validator.sandbox.schema import AgentVersion, EvaluationRun, SwebenchProblem
from validator.sandbox.constants import AGENTS_BASE_DIR
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

def load_swebench_problems() -> dict[str, SwebenchProblem]:
    """Load SWE-bench problems from local JSON file"""
    import json
    
    swe_bench_file = Path(__file__).parent.parent.parent / "swe_bench_verified.json"
    with open(swe_bench_file, 'r') as f:
        instances = json.load(f)
    
    problems = {}
    for instance in instances:
        problems[instance["instance_id"]] = SwebenchProblem(
            instance_id=instance["instance_id"],
            problem_statement=instance["problem_statement"],
            repo=instance["repo"],
            base_commit=instance["base_commit"],
            test_patch=instance["test_patch"],
        )
    
    return problems

@tracer.wrap(resource="run-evaluation")
async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, agent_version: AgentVersion, evaluation_runs: List[EvaluationRun]):
    """Run evaluation for a specific agent version"""
    logger.info(f"Starting evaluation {evaluation_id} for agent {agent_version.miner_hotkey}")

    sandbox_manager = SandboxManager(websocket_app, evaluation_id)
    websocket_app.sandbox_manager = sandbox_manager  # Store reference for cancellation
    errored = False

    try:
        # Download agent code
        agent_dir = AGENTS_BASE_DIR / agent_version.miner_hotkey / str(agent_version.version_num)
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agent_dir / "agent.py"
        async with get_shared_client() as client:
            logger.info(f"Downloading agent code for version {agent_version.version_id}")
            response = await client.get(
                f"{RIDGES_API_URL}/retrieval/agent-version-file",
                params={"version_id": agent_version.version_id},
            )
            response.raise_for_status()
            agent_file.write_bytes(response.content)

        
        # Get problems for the evaluation runs
        all_problems = load_swebench_problems()
        problems = {
            evaluation_run.swebench_instance_id: all_problems[evaluation_run.swebench_instance_id.strip()]
            for evaluation_run in evaluation_runs
        }

        for evaluation_run in evaluation_runs:
            problem = problems[evaluation_run.swebench_instance_id]
            await sandbox_manager.create_sandbox(evaluation_run, problem, agent_dir)

        # Start monitoring for platform-side cancellations
        sandbox_manager.start_cancellation_monitoring()
        
        await sandbox_manager.run_all_sandboxes()
        logger.info(f"Evaluation {evaluation_id} completed successfully")
    except asyncio.CancelledError:
        logger.info(f"Evaluation {evaluation_id} was cancelled")
        # Only treat as error if not cancelled by platform
        errored = not sandbox_manager._cancelled_by_platform
        if sandbox_manager._cancelled_by_platform:
            logger.info(f"Evaluation {evaluation_id} was cancelled by platform - this is normal")
        raise  # Re-raise CancelledError so it can be handled by the websocket app
    except Exception as e:
        logger.error(f"Error during evaluation: {e}", exc_info=True)
        errored = True
    finally:
        sandbox_manager.cleanup(force_cancel=errored)
