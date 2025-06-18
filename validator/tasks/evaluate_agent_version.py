"""Task for running agents in sandboxes."""

from datetime import datetime
import json
import os
import tempfile
from typing import List
import uuid
import httpx
from validator.db.schema import AgentVersion, EvaluationRun
from validator.dependancies import get_db_session
from validator.sandbox.manager import SandboxManager
from shared.logging_utils import get_logger
from validator.config import RIDGES_API_URL, CHALLENGE_TIMEOUT, validator_hotkey
from swebench.harness.run_evaluation import (
    load_swebench_dataset,
    run_instance,
    make_test_spec
)
import docker
from swebench.harness.docker_build import build_env_images
import asyncio

logger = get_logger(__name__)


async def evaluate_agent_version(agent_version: AgentVersion, evaluation_running: asyncio.Event):
    """Run agents in sandboxes and collect their outputs."""
    logger.info("Running sandboxes")
    evaluation_running.set()
    try:
        # Create sandbox manager
        sbox_manager = SandboxManager()

        # Create a client with longer timeout for agent operations
        async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds() * 2) as client:
            try:
                # Download the agent code from Ridges API
                logger.info(f"Downloading agent code for agent {agent_version.agent_id} version {agent_version.latest_version}")
                response = await client.get(
                    f"{RIDGES_API_URL}/retrieval/agent-version-file",
                    params={"version_id": agent_version.version_id},
                )
                response.raise_for_status()
                logger.info(f"Downloaded agent code for agent {agent_version.agent_id} version {agent_version.latest_version}")

                # Create a temp directory for the agent code
                temp_dir = tempfile.mkdtemp()
                agent_file_path = os.path.join(temp_dir, "agent.py")

                # Save the Python file directly
                logger.info(f"Saving agent code to {agent_file_path}")
                with open(agent_file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Saved agent code for agent {agent_version.agent_id} version {agent_version.latest_version}")

                instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", ["astropy__astropy-14309"])
                for instance in instances:
                    sbox = sbox_manager.add_sandbox(instance["instance_id"], src_dir=temp_dir, repo_dir_path=agent_file_path)
                    sbox.run_async({"instance_id": instance["instance_id"]})
            except Exception as e:
                logger.error(
                    f"Error configuring sandbox for agent {agent_version.agent_id} version {agent_version.latest_version}: {e}"
                )

        # Wait for all sandboxes to finish
        logger.info("Waiting on sandboxes...")
        sbox_manager.wait_for_all_sandboxes()

        # Run evaluation
        logger.info("Running evaluation...")
        client = docker.from_env()

        runs: List[EvaluationRun] = []

        for instance_id, patch in sbox_manager.get_successful_patches():
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": f"{agent_version.agent_id}v{agent_version.latest_version}",
                "model_patch": patch
            }
        
            instance = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", [instance_id])[0]
            if not instance:
                logger.error(f"Instance {instance_id} not found in dataset")
                continue
            
            # Create test spec for our instance
            test_spec = make_test_spec(instance)
            build_env_images(client, [test_spec], max_workers=1)

            started_at = datetime.now()

            run_result = run_instance(
                test_spec=test_spec,
                pred=prediction,
                rm_image=True,  # Clean up after each run
                force_rebuild=False,
                client=client,
                run_id="ridges_run",
                timeout=1800,
                rewrite_reports=False
            )
            if run_result:
                instance_id, report = run_result
                report = report[instance_id]
                evaluation_run = EvaluationRun(
                    run_id=str(uuid.uuid4()),
                    version_id=agent_version.version_id,
                    validator_hotkey=validator_hotkey.ss58_address,
                    swebench_instance_id=instance_id,
                    fail_to_pass_success=json.dumps(report["tests_status"]["FAIL_TO_PASS"]["success"]),
                    pass_to_pass_success=json.dumps(report["tests_status"]["PASS_TO_PASS"]["success"]),
                    fail_to_fail_success=json.dumps(report["tests_status"]["FAIL_TO_FAIL"]["success"]),
                    pass_to_fail_success=json.dumps(report["tests_status"]["PASS_TO_FAIL"]["success"]),
                    response=patch,
                    solved=report["resolved"],
                    started_at=started_at,
                    finished_at=datetime.now()
                )

                runs.append(evaluation_run)
            else:
                logger.info(f"Agent {agent_version.agent_id} version {agent_version.latest_version} failed to run instance {instance_id}")
                evaluation_run = EvaluationRun(
                    run_id=str(uuid.uuid4()),
                    version_id=agent_version.version_id,
                    validator_hotkey=validator_hotkey.ss58_address,
                    swebench_instance_id=instance_id,
                    fail_to_pass_success=None,
                    pass_to_pass_success=None,
                    fail_to_fail_success=None,
                    pass_to_fail_success=None,
                    response=None,
                    solved=False,
                    started_at=started_at,
                    finished_at=datetime.now()
                )
                runs.append(evaluation_run)
        
        logger.info(f"Runs: {runs}")

        # Save evaluation runs to database
        if runs:
            with get_db_session() as session:
                session.add_all(runs)
                session.commit()
            logger.info(f"Saved {len(runs)} evaluation runs to database")

        sbox_manager.cleanup()
    finally:
        evaluation_running.clear()
