"""Task for running agents in sandboxes."""

from datetime import datetime
import json
import os
import tempfile
from typing import TYPE_CHECKING, List
import uuid
import httpx
from validator.db.schema import AgentVersion, EvaluationRun
from validator.dependencies import get_session_factory
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
    try:
        # Create sandbox manager
        sbox_manager = SandboxManager()

        # Create a client with longer timeout for agent operations
        async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds() * 2) as client:
            try:
                # Download the agent code from Ridges API
                logger.info(f"Downloading agent code for agent {agent_version.agent_id} version {agent_version.version_num}")
                response = await client.get(
                    f"{RIDGES_API_URL}/retrieval/agent-version-file",
                    params={"version_id": agent_version.version_id},
                )
                response.raise_for_status()
                logger.info(f"Downloaded agent code for agent {agent_version.agent_id} version {agent_version.version_num}")

                # Create a temp directory for the agent code
                temp_dir = tempfile.mkdtemp()
                agent_file_path = os.path.join(temp_dir, "agent.py")

                # Save the Python file directly
                logger.info(f"Saving agent code to {agent_file_path}")
                with open(agent_file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Saved agent code for agent {agent_version.agent_id} version {agent_version.version_num}")

                instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", ["astropy__astropy-14309"])
                for instance in instances:
                    sbox = sbox_manager.add_sandbox(instance["instance_id"], src_dir=temp_dir, repo_dir_path=agent_file_path)
                    await sbox.run_async({"instance_id": instance["instance_id"]})
            except Exception as e:
                logger.error(
                    f"Error configuring sandbox for agent {agent_version.agent_id} version {agent_version.version_num}: {e}"
                )

        # Wait for all sandboxes to finish
        logger.info("Waiting on sandboxes...")
        await sbox_manager.wait_for_all_sandboxes()

        # Run evaluation
        logger.info("Running evaluation...")
        client = docker.from_env()

        runs: List[EvaluationRun] = []

        for success, instance_id, patch, error in sbox_manager.get_patches_and_errors():
            evaluation_run = EvaluationRun(
                run_id=str(uuid.uuid4()),
                evaluation_id=evaluation_id,
                validator_hotkey=validator_hotkey.ss58_address,
                swebench_instance_id=instance_id,
                fail_to_pass_success=None,
                pass_to_pass_success=None,
                fail_to_fail_success=None,
                pass_to_fail_success=None,
                response=None,
                error=None,
                solved=None,
                started_at=datetime.now(),
                finished_at=None
            )

            if not success:
                evaluation_run.error=error
                evaluation_run.solved=False
                evaluation_run.finished_at=datetime.now()
                await websocket_app.send({"event": "upsert-evaluation-run", "evaluation_run": evaluation_run.to_dict()})
                continue
            
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": f"{agent_version.agent_id}v{agent_version.version_num}",
                "model_patch": patch
            }
        
            instance = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", [instance_id])[0]
            if not instance:
                logger.error(f"Instance {instance_id} not found in dataset")
                continue
            
            # Create test spec for our instance
            test_spec = make_test_spec(instance)
            build_env_images(client, [test_spec], max_workers=1)

            await websocket_app.send({"event": "upsert-evaluation-run", "evaluation_run": evaluation_run.to_dict()}) # Run started

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

            evaluation_run.finished_at=datetime.now()
            if run_result:
                instance_id, report = run_result
                report = report[instance_id]
                evaluation_run.fail_to_pass_success=json.dumps(report["tests_status"]["FAIL_TO_PASS"]["success"])
                evaluation_run.pass_to_pass_success=json.dumps(report["tests_status"]["PASS_TO_PASS"]["success"])
                evaluation_run.fail_to_fail_success=json.dumps(report["tests_status"]["FAIL_TO_FAIL"]["success"])
                evaluation_run.pass_to_fail_success=json.dumps(report["tests_status"]["PASS_TO_FAIL"]["success"])
                evaluation_run.response=patch
                evaluation_run.solved=report["resolved"]
            else:
                logger.info(f"Agent {agent_version.agent_id} version {agent_version.version_num} failed to run instance {instance_id}")
                evaluation_run.solved=False
                evaluation_run.error=error
                # with open(f"logs/run_evalulation/{evaluation_run.run_id}/{evaluation_run.run_id}.log", "r") as f:
                #     evaluation_run.error = f.read()
            
            runs.append(evaluation_run)

            await websocket_app.send({"event": "upsert-evaluation-run", "evaluation_run": evaluation_run.to_dict()}) # Run finished
            
        # Save evaluation runs to database
        if runs:
            SessionFactory = get_session_factory()
            session = SessionFactory()
            try:
                session.add_all(runs)
                session.commit()
                logger.info(f"Saved {len(runs)} evaluation runs to database")
            finally:
                session.close()
    except Exception as e:
        logger.error(f"Error evaluating agent version: {e}")
        errored = True
    finally:
        sbox_manager.cleanup()
        await websocket_app.send({
            "event": "finish-evaluation",
            "evaluation_id": evaluation_id,
            "errored": errored,
        })
        websocket_app.evaluation_running.clear()
