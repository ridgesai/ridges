import ast
import asyncio
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional
import time

from docker.models.containers import Container
from swebench.harness.docker_build import build_env_images
from swebench.harness.run_evaluation import (
    load_swebench_dataset,
    make_test_spec,
    run_instance,
)

from validator.sandbox.clone_repo import clone_repo
from validator.utils.logging import get_logger
from validator.sandbox.schema import EvaluationRun
from validator.sandbox.constants import (
    MAIN_FILE, PROXY_CONTAINER_NAME, REPOS_BASE_DIR, SANDBOX_DIR, 
    SANDBOX_DOCKER_IMAGE, SANDBOX_INPUT_FILE, SANDBOX_MAIN_FILE, 
    SANDBOX_NETWORK_NAME, SANDBOX_OUTPUT_FILE, SANDBOX_REPO_DIR, 
    SANDBOX_SOURCE_DIR
)

if TYPE_CHECKING:
    from validator.sandbox.manager import SandboxManager

logger = get_logger(__name__)


class Sandbox:
    evaluation_run: "EvaluationRun"
    agent_dir: Path
    repo_dir_path: Path = None
    manager: "SandboxManager"
    container: Optional["Container"] = None
    running: bool
    cpu_usage: float = 0
    ram_usage: float = 0
    _task: Optional[asyncio.Task] = None
    start_time: Optional[datetime] = None

    def __init__(
        self,
        evaluation_run: "EvaluationRun",
        agent_dir: Path,
        manager: "SandboxManager",
    ):
        self.evaluation_run = evaluation_run
        self.agent_dir = agent_dir.absolute()
        self.manager = manager

        self.running = False
        self.start_time = None

        try:
            validate_sandbox_dir(agent_dir)
        except ValueError as e:
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now()
            self.evaluation_run.error = str(e)
            manager.websocket_app.send(
                {
                    "event": "upsert-evaluation-run",
                    "evaluation_run": self.evaluation_run.to_dict(),
                }
            )

    async def run(self, challenge: dict):
        self.running = True
        self.start_time = time.time()

        async def _async_main():
            logger.info(f"Running sandbox for run {self.evaluation_run.run_id}")

            self.evaluation_run.status = "sandbox_created"
            self.evaluation_run.sandbox_created_at = datetime.now()
            await self.manager.websocket_app.send(
                {
                    "event": "upsert-evaluation-run",
                    "evaluation_run": self.evaluation_run.to_dict(),
                }
            )

            await asyncio.to_thread(self._run, challenge)

            # Send update after patch generation (or error)
            await self.manager.websocket_app.send(
                {
                    "event": "upsert-evaluation-run",
                    "evaluation_run": self.evaluation_run.to_dict(),
                }
            )

            if self.evaluation_run.error:
                logger.error(
                    f"Sandbox for run {self.evaluation_run.run_id} encountered error during patch generation: {self.evaluation_run.error}"
                )
            elif self.evaluation_run.response:
                logger.info(
                    f"Sandbox for run {self.evaluation_run.run_id} generated patch successfully; starting evaluation."
                )

                self.evaluation_run.status = "eval_started"
                self.evaluation_run.eval_started_at = datetime.now()
                await self.manager.websocket_app.send(
                    {
                        "event": "upsert-evaluation-run",
                        "evaluation_run": self.evaluation_run.to_dict(),
                    }
                )

                # ---------------- Evaluation ----------------
                await asyncio.to_thread(self._run_evaluation)

                # Send update after evaluation completes
                await self.manager.websocket_app.send(
                    {
                        "event": "upsert-evaluation-run",
                        "evaluation_run": self.evaluation_run.to_dict(),
                    }
                )

        self._task = asyncio.create_task(_async_main())

    async def wait(self):
        if not self._task:
            logger.warning(
                f"Sandbox {self.evaluation_run.run_id} has no task to wait for"
            )
        if self.running and hasattr(self, "_task") and self._task:
            await self._task

    def _run(self, challenge: dict):
        if self.evaluation_run.error is not None:
            self.running = False
            return

        try:
            # Create an input and output file on the host filesystem
            input_file = self.agent_dir / "input.json"
            output_file = self.agent_dir / "output.json"

            # Write the input to the input file
            with open(input_file, "w") as f:
                json.dump(challenge, f)

            repo_name = challenge.get("repo")
            base_commit = challenge.get("base_commit")

            self.repo_dir_path = REPOS_BASE_DIR / self.evaluation_run.run_id
            self.repo_dir_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Cloning repository {repo_name} into {self.repo_dir_path}")
            clone_repo(self.repo_dir_path, repo_name, base_commit)

            self.container = self.manager.docker.containers.run(
                image=SANDBOX_DOCKER_IMAGE,
                network=SANDBOX_NETWORK_NAME,
                volumes={
                    # Mount the appropriate files
                    os.path.abspath(MAIN_FILE): {
                        "bind": SANDBOX_MAIN_FILE,
                        "mode": "ro",
                    },
                    input_file: {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                    output_file: {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},
                    # Mount the source directory
                    os.path.abspath(self.agent_dir): {
                        "bind": SANDBOX_SOURCE_DIR,
                        "mode": "ro",
                    },
                    os.path.abspath(self.repo_dir_path): {
                        "bind": SANDBOX_REPO_DIR,
                        "mode": "rw",
                    },  # Since tool calls can modify the repo
                },
                working_dir=SANDBOX_DIR,
                environment={"AI_PROXY_URL": f"http://{PROXY_CONTAINER_NAME}"},
                detach=True,
            )

            # Wait for the container to finish running, then kill it
            self.container.wait()
            self.container.remove()

            self.evaluation_run.status = "patch_generated"
            self.evaluation_run.patch_generated_at = datetime.now()

            if not self.evaluation_run.error:
                with open(output_file, "r") as f:
                    try:
                        output = json.load(f)
                        logger.info(f"Output: {output}")
                        if output.get("success"):
                            self.evaluation_run.response = output.get("output").get(
                                "patch"
                            )
                            if self.evaluation_run.response == "":
                                self.evaluation_run.status = "result_scored"
                                self.evaluation_run.solved = False
                                self.evaluation_run.result_scored_at = datetime.now()
                                self.evaluation_run.error = (
                                    "Empty patch returned from agent.py"
                                )
                        else:
                            self.evaluation_run.status = "result_scored"
                            self.evaluation_run.solved = False
                            self.evaluation_run.result_scored_at = datetime.now()
                            self.evaluation_run.error = output.get("error")
                    except Exception as e:
                        self.evaluation_run.status = "result_scored"
                        self.evaluation_run.solved = False
                        self.evaluation_run.result_scored_at = datetime.now()
                        self.evaluation_run.error = "JSON parsing error: " + str(e)

        except Exception as e:
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.solved = False
            self.evaluation_run.result_scored_at = datetime.now()
            self.evaluation_run.error = str(e)
        finally:
            self.running = False

    def _run_evaluation(self):
        """Blocking helper that runs evaluation for this sandbox's run."""
        try:
            # Perform evaluation within the sandbox itself
            self._evaluate_run()
        except Exception as e:
            # Capture evaluation errors so they don't crash the event-loop thread
            self.evaluation_run.solved = False
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now()
            self.evaluation_run.error = str(e)

    def _evaluate_run(self):
        try:
            # Mark evaluation start
            self.evaluation_run.status = "eval_started"
            self.evaluation_run.eval_started_at = datetime.now()

            # Fetch the corresponding SWE-bench instance
            instance_id = self.evaluation_run.swebench_instance_id
            instance = load_swebench_dataset(
                "SWE-bench/SWE-bench_Verified", "test", [instance_id]
            )[0]
            if not instance:
                raise RuntimeError(
                    f"Instance {instance_id} not found in SWE-bench dataset"
                )

            # Prepare prediction & test spec
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": self.evaluation_run.run_id,
                "model_patch": self.evaluation_run.response,
            }

            test_spec = make_test_spec(instance)

            # Build the environment image for this instance (cached if already built)
            build_env_images(self.manager.docker, [test_spec], max_workers=4)

            # Execute the evaluation
            run_result = run_instance(
                test_spec=test_spec,
                pred=prediction,
                rm_image=False,  # Clean up after each run
                force_rebuild=False,
                client=self.manager.docker,
                run_id=self.evaluation_run.run_id,
                timeout=1800,
                rewrite_reports=False,
            )

            # Parse results
            if run_result:
                instance_id, report = run_result
                report = report[instance_id]

                if report["patch_is_None"]:
                    self.evaluation_run.solved = False
                    self.evaluation_run.error = "Patch was empty"
                else:
                    self.evaluation_run.fail_to_pass_success = json.dumps(
                        report["tests_status"]["FAIL_TO_PASS"]["success"]
                    )
                    self.evaluation_run.pass_to_pass_success = json.dumps(
                        report["tests_status"]["PASS_TO_PASS"]["success"]
                    )
                    self.evaluation_run.fail_to_fail_success = json.dumps(
                        report["tests_status"]["FAIL_TO_FAIL"]["success"]
                    )
                    self.evaluation_run.pass_to_fail_success = json.dumps(
                        report["tests_status"]["PASS_TO_FAIL"]["success"]
                    )
                    self.evaluation_run.solved = report["resolved"]
            else:
                self.evaluation_run.solved = False
                self.evaluation_run.error = (
                    self._get_patch_apply_error() or "Patch did not apply"
                )

        except Exception as e:
            self.evaluation_run.solved = False
            self.evaluation_run.error = str(e)
        finally:
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now()

    def _get_patch_apply_error(self) -> str | None:
        patch_path = Path(tempfile.mkstemp(suffix=".patch")[1])
        patch_path.write_text(self.evaluation_run.response)
        branch = f"patch-test-{uuid.uuid4().hex[:8]}"
        try:
            current_commit_hash = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip()
            subprocess.run(
                ["git", "checkout", "-b", branch, current_commit_hash],
                cwd=self.repo_dir_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            result = subprocess.run(
                [
                    "git",
                    "apply",
                    "--verbose",
                    "--reject",
                    "--unidiff-zero",
                    str(patch_path),
                ],
                cwd=self.repo_dir_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            return result.stdout.strip() if result.returncode else None
        except subprocess.CalledProcessError as e:
            return e.stderr.strip() or str(e)
        finally:
            subprocess.run(
                ["git", "checkout", "-"],
                cwd=self.repo_dir_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self.repo_dir_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            patch_path.unlink(missing_ok=True)


def validate_sandbox_dir(dir_path: str) -> None:
    """
    Checks if the given directory is in the appropriate format for a sandbox.
    Returns None if the directory is valid, otherwise raises a ValueError.
    """

    # First, check if the directory exists.
    if not os.path.isdir(dir_path):
        raise ValueError(f"Failed to find {dir_path}")

    # Then, check if the agent.py file exists
    agent_main_path = os.path.join(dir_path, "agent.py")
    if not os.path.isfile(agent_main_path):
        raise ValueError(f"Failed to find agent.py")

    # Then, parse the agent.py file
    try:
        with open(agent_main_path, "r") as f:
            tree = ast.parse(f.read())
    except Exception as e:
        raise ValueError(f"Failed to parse agent.py: {str(e)}")

    # Finally, look for top-level agent_main function
    found_agent_main = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "agent_main":
            args = node.args
            if (
                len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs) == 1
                and not args.vararg
                and not args.kwarg
            ):
                found_agent_main = True
                break

    if not found_agent_main:
        raise ValueError("Failed to find agent_main()")
