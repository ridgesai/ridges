"""Task for running agents in sandboxes."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, List
import uuid
import httpx
from validator.db.schema import AgentVersion, EvaluationRun
from validator.dependencies import get_session_factory
from validator.sandbox.manager import SandboxManager
from shared.logging_utils import get_logger
from validator.config import EASY_INSTANCES, RIDGES_API_URL, CHALLENGE_TIMEOUT, validator_hotkey, MEDIUM_INSTANCES
from swebench.harness.run_evaluation import load_swebench_dataset

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)


async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, agent_version: AgentVersion):
    """Run agents in sandboxes and collect their outputs."""

    websocket_app.evaluation_running.set()
    logger.info("Running sandboxes")

    await websocket_app.send({
        "event": "start-evaluation",
        "evaluation_id": evaluation_id,
    })

    errored = False
    sbox_manager = None  # Initialize to None to avoid UnboundLocalError
    try:
        # Create sandbox manager
        sbox_manager = SandboxManager(websocket_app)

        # Create a client with longer timeout for agent operations
        async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds() * 2) as client:
            try:
                # Download the agent code from Ridges API
                logger.info(f"Downloading agent code for agent {agent_version.agent_id} version {agent_version.version_num}")
                try:
                    response = await client.get(
                        f"{RIDGES_API_URL}/retrieval/agent-version-file",
                        params={"version_id": agent_version.version_id},
                    )
                    response.raise_for_status()
                    logger.info(f"Downloaded agent code for agent {agent_version.agent_id} version {agent_version.version_num}")
                except Exception as e:
                    logger.error(f"Failed to download from {RIDGES_API_URL}/retrieval/agent-version-file")
                    logger.error(f"Error downloading agent code for agent {agent_version.agent_id} version {agent_version.version_num}: {e}")
                    raise e

                # Create a temp directory for the agent code
                temp_dir = tempfile.mkdtemp()
                agent_file_path = os.path.join(temp_dir, "agent.py")

                # Save the Python file directly
                logger.info(f"Saving agent code to {agent_file_path}")
                with open(agent_file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Saved agent code for agent {agent_version.agent_id} version {agent_version.version_num}")

                instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", EASY_INSTANCES + MEDIUM_INSTANCES)
                for instance in instances:
                    evaluation_run = EvaluationRun(
                        run_id=str(uuid.uuid4()),
                        evaluation_id=evaluation_id,
                        validator_hotkey=validator_hotkey.ss58_address,
                        swebench_instance_id=instance["instance_id"],
                        fail_to_pass_success=None,
                        pass_to_pass_success=None,
                        fail_to_fail_success=None,
                        pass_to_fail_success=None,
                        response=None,
                        error=None,
                        solved=None,
                        status="started",
                        started_at=datetime.now(),
                        sandbox_created_at=None,
                        patch_generated_at=None,
                        eval_started_at=None,
                        result_scored_at=None
                    )
                    await websocket_app.send({"event": "upsert-evaluation-run", "evaluation_run": evaluation_run.to_dict()})
                    sbox = sbox_manager.add_sandbox(evaluation_run, src_dir=Path(temp_dir))
                    await sbox.run({
                        "run_id": evaluation_run.run_id,
                        "problem_statement": instance["problem_statement"],
                        "repo": instance["repo"],
                        "base_commit": instance["base_commit"]
                    })

            except Exception as e:
                logger.error(
                    f"Error configuring sandbox for agent {agent_version.agent_id} version {agent_version.version_num}: {e}",
                    exc_info=True,
                    stack_info=True
                )

        # Wait for all sandboxes (which now also run evaluation) to finish
        logger.info("Waiting on sandboxes (patch generation + evaluation)...")
        await sbox_manager.wait_for_all_sandboxes()

    except Exception as e:
        logger.error(f"Error evaluating agent version: {e}", exc_info=True, stack_info=True)
        errored = True
    finally:
        if sbox_manager:
            sbox_manager.cleanup()
        await websocket_app.send({
            "event": "finish-evaluation",
            "evaluation_id": evaluation_id,
            "errored": errored,
        })
        websocket_app.evaluation_running.clear()
