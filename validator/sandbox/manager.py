import os
import json
import shutil
import subprocess
import tempfile
import time
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, List, Tuple, Optional
from pathlib import Path
import uuid
from shared.logging_utils import get_logger
import docker
from docker import DockerClient
from docker.models.containers import Container

from validator.config import RIDGES_API_URL
from validator.sandbox.schema import EvaluationRun
from validator.sandbox.clone_repo import clone_repo
from validator.utils.temp_files import create_temp_file
from swebench.harness.run_evaluation import load_swebench_dataset, run_instance, make_test_spec
from swebench.harness.docker_build import build_env_images

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

# Set up logger
logger = get_logger(__name__)

# Get the current directory where this file is located
CURRENT_DIR = Path(__file__).parent.absolute()

# The path (on the host filesystem) to the main script that will run in the sandbox
MAIN_FILE = str(CURRENT_DIR / "Main.py")

# The Docker image to use for the sandbox
# docker build -t sandbox-runner .
SANDBOX_DOCKER_IMAGE = "sandbox-runner"

# The mounted directories/files (these paths exist only in the sandbox)
# The real paths are stored in the Sandbox object but the mounted paths are constant
SANDBOX_DIR = "/sandbox"
SANDBOX_MAIN_FILE = SANDBOX_DIR + "/Main.py"
SANDBOX_INPUT_FILE = SANDBOX_DIR + "/input.json"
SANDBOX_OUTPUT_FILE = SANDBOX_DIR + "/output.json"

# The mounted directories/files that come from the agent's submitted code
SANDBOX_SOURCE_DIR = SANDBOX_DIR + "/src"
SANDBOX_SOURCE_AGENT_MAIN_FILE = SANDBOX_SOURCE_DIR + "/agent.py" # NOTE: We don't actually mount this, we just expect that it exists

# The mounted directory that contains the repository that the agent is solving a problem for
SANDBOX_REPO_DIR = SANDBOX_DIR + "/repo"

# The maximum resource usage that is allowed for a sandbox
SANDBOX_MAX_CPU_USAGE = 90 # %
SANDBOX_MAX_RAM_USAGE = 512 * 4 # MiB 
SANDBOX_MAX_RUNTIME = 20 * 60 # seconds

# The name of the network that the sandbox will be connected to
SANDBOX_NETWORK_NAME = "sandbox-network"

# Nginx proxy image & container details
PROXY_DOCKER_IMAGE = "sandbox-nginx-proxy"
PROXY_CONTAINER_NAME = "sandbox-proxy"

# Directory to cache cloned repositories for reuse across validations
# Repositories will be stored at validator/repos/<org>/<repo>
REPOS_BASE_DIR = Path(__file__).parent.parent / "repos"

import os
import ast

def validate_sandbox_dir(dir_path: str) -> None:
    """
    Checks if the given directory is in the appropriate format for a sandbox.
    Returns None if the directory is valid, otherwise raises a ValueError.
    """

    # First, check if the directory exists.
    if not os.path.isdir(dir_path):
        raise ValueError(f'Failed to find {dir_path}')

    # Then, check if the agent.py file exists
    agent_main_path = os.path.join(dir_path, 'agent.py')
    if not os.path.isfile(agent_main_path):
        raise ValueError(f'Failed to find agent.py')

    # Then, parse the agent.py file
    try:
        with open(agent_main_path, 'r') as f:
            tree = ast.parse(f.read())
    except Exception as e:
        raise ValueError(f'Failed to parse agent.py: {str(e)}')

    # Finally, look for top-level agent_main function
    found_agent_main = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'agent_main':
            args = node.args
            if len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs) == 1 and not args.vararg and not args.kwarg:
                found_agent_main = True
                break

    if not found_agent_main:
        raise ValueError('Failed to find agent_main()')
    
class Sandbox:
    evaluation_run: 'EvaluationRun'
    src_dir: Path
    repo_dir_path: Path = None
    manager: 'SandboxManager'
    container: Optional["Container"] = None
    running: bool
    cpu_usage: float = 0
    ram_usage: float = 0
    _task: Optional[asyncio.Task] = None

    def __init__(self, evaluation_run: 'EvaluationRun', src_dir: Path, manager: 'SandboxManager'):        
        self.evaluation_run = evaluation_run
        self.src_dir = src_dir.absolute()
        self.manager = manager

        self.running = False

        try:
            validate_sandbox_dir(src_dir)
        except ValueError as e:
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now()
            self.evaluation_run.error = str(e)
            manager.websocket_app.send({
                "event": "upsert-evaluation-run",
                "evaluation_run": self.evaluation_run.to_dict()
            })

    async def run(self, challenge: dict):
        self.running = True
        
        async def _async_main():
            logger.info(f'Running sandbox for run {self.evaluation_run.run_id}')
            self.evaluation_run.status = "sandbox_created"
            self.evaluation_run.sandbox_created_at = datetime.now()
            await self.manager.websocket_app.send({
                "event": "upsert-evaluation-run",
                "evaluation_run": self.evaluation_run.to_dict()
            })

            await asyncio.to_thread(self._run, challenge)

            # Send update after patch generation (or error)
            await self.manager.websocket_app.send({
                "event": "upsert-evaluation-run",
                "evaluation_run": self.evaluation_run.to_dict()
            })

            if self.evaluation_run.error:
                logger.error(f'Sandbox for run {self.evaluation_run.run_id} encountered error during patch generation: {self.evaluation_run.error}')
            elif self.evaluation_run.response:
                logger.info(f'Sandbox for run {self.evaluation_run.run_id} generated patch successfully; starting evaluation.')

                self.evaluation_run.status = "eval_started"
                self.evaluation_run.eval_started_at = datetime.now()
                await self.manager.websocket_app.send({
                    "event": "upsert-evaluation-run",
                    "evaluation_run": self.evaluation_run.to_dict()
                })

                # ---------------- Evaluation ----------------
                await asyncio.to_thread(self._run_evaluation)

                # Send update after evaluation completes
                await self.manager.websocket_app.send({
                    "event": "upsert-evaluation-run",
                    "evaluation_run": self.evaluation_run.to_dict()
                })

        self._task = asyncio.create_task(_async_main())
    
    async def wait(self):
        if not self._task:
            logger.warning(f"Sandbox {self.evaluation_run.run_id} has no task to wait for")
        if self.running and hasattr(self, '_task') and self._task:
            await self._task

    def _run(self, challenge: dict):
        if (self.evaluation_run.error is not None):
            self.running = False
            return
        
        try:
            # Create an input and output file on the host filesystem
            input_file = create_temp_file()
            output_file = create_temp_file()

            # Write the input to the input file
            with open(input_file, 'w') as f:
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
                    os.path.abspath(MAIN_FILE): {"bind": SANDBOX_MAIN_FILE, "mode": "ro"},
                    input_file: {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                    output_file: {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},

                    # Mount the source directory
                    os.path.abspath(self.src_dir): {"bind": SANDBOX_SOURCE_DIR, "mode": "ro"},
                    os.path.abspath(self.repo_dir_path): {"bind": SANDBOX_REPO_DIR, "mode": "rw"}, # Since tool calls can modify the repo
                },
                working_dir=SANDBOX_DIR,
                environment={
                    "AI_PROXY_URL": f"http://{PROXY_CONTAINER_NAME}"
                },
                detach=True
            )

            # Wait for the container to finish running, then kill it
            self.container.wait()
            self.container.remove()

            self.evaluation_run.status = "patch_generated"
            self.evaluation_run.patch_generated_at = datetime.now()

            if not self.evaluation_run.error:
                with open(output_file, 'r') as f:
                    try:
                        output = json.load(f)
                        logger.info(f"Output: {output}")
                        if (output.get('success')):
                            self.evaluation_run.response = output.get('output').get('patch')
                            if self.evaluation_run.response == "":
                                self.evaluation_run.status = "result_scored"
                                self.evaluation_run.solved = False
                                self.evaluation_run.result_scored_at = datetime.now()
                                self.evaluation_run.error = "Empty patch returned from agent.py"
                        else:
                            self.evaluation_run.status = "result_scored"
                            self.evaluation_run.solved = False
                            self.evaluation_run.result_scored_at = datetime.now()
                            self.evaluation_run.error = output.get('error')
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
            os.remove(input_file)
            os.remove(output_file)
            self.running = False
    
    def cleanup(self):
        # Clean up temporary agent source directory
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.repo_dir_path, ignore_errors=True)

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
                raise RuntimeError(f"Instance {instance_id} not found in SWE-bench dataset")

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
                    self.evaluation_run.fail_to_pass_success = json.dumps(report["tests_status"]["FAIL_TO_PASS"]["success"])
                    self.evaluation_run.pass_to_pass_success = json.dumps(report["tests_status"]["PASS_TO_PASS"]["success"])
                    self.evaluation_run.fail_to_fail_success = json.dumps(report["tests_status"]["FAIL_TO_FAIL"]["success"])
                    self.evaluation_run.pass_to_fail_success = json.dumps(report["tests_status"]["PASS_TO_FAIL"]["success"])
                    self.evaluation_run.solved = report["resolved"]
            else:
                self.evaluation_run.solved = False
                self.evaluation_run.error = self._get_patch_apply_error() or "Patch did not apply"

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
            current_commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
            subprocess.run(["git", "checkout", "-b", branch, current_commit_hash], cwd=self.repo_dir_path, check=True,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = subprocess.run(["git", "apply", "--verbose", "--reject", "--unidiff-zero", str(patch_path)],
                                    cwd=self.repo_dir_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return result.stdout.strip() if result.returncode else None
        except subprocess.CalledProcessError as e:
            return e.stderr.strip() or str(e)
        finally:
            subprocess.run(["git", "checkout", "-"], cwd=self.repo_dir_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "branch", "-D", branch], cwd=self.repo_dir_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            patch_path.unlink(missing_ok=True)




class SandboxManager:
    websocket_app: "WebsocketApp"
    proxy_container: Optional["Container"] = None
    docker: DockerClient
    sandboxes: List[Sandbox]
    _monitor_task: asyncio.Task

    def __init__(self, websocket_app: "WebsocketApp"):
        self.websocket_app = websocket_app
        self.docker = docker.from_env(max_pool_size=100)
        self.create_internal_network()
        self.proxy_container = self._start_proxy_container()
        logger.info("Nginx proxy container started successfully")
            
        self.sandboxes = []
        self._monitor_task = asyncio.create_task(self._monitor())

    def create_internal_network(self):
        """
        Create (or ensure) an isolated internal sandbox network – containers on this
        network have *no* external connectivity.
        """
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
        except docker.errors.NotFound:
            logger.info(f"Creating isolated docker network '{SANDBOX_NETWORK_NAME}' (internal=True)")
            self.docker.networks.create(SANDBOX_NETWORK_NAME, driver='bridge', internal=True)
    
    async def _monitor(self):
        while True:
            for sandbox in self.sandboxes:
                if sandbox.running and sandbox.evaluation_run.error is None:
                    try:
                        await asyncio.to_thread(self._monitor_sandbox, sandbox)
                    except Exception:
                        continue
            
            await asyncio.sleep(1)

    def _monitor_sandbox(self, sandbox: Sandbox):
        # Get the stats
        stats = sandbox.container.stats(stream=False)
        
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        
        sandbox.cpu_usage = cpu_delta / system_delta * 100
        sandbox.ram_usage = stats["memory_stats"]["usage"] / (1024 * 1024)

        current_time = time.time()
        runtime = current_time - sandbox.start_time

        # logger.debug(f'sandbox {sandbox.id}: cpu: {sandbox.cpu_usage:.1f}, ram: {sandbox.ram_usage:.1f} MiB, runtime: {runtime:.1f} seconds') 
        
        # Check if the sandbox is using too many resources, if so, kill it and set the error
        # if sandbox.cpu_usage > SANDBOX_MAX_CPU_USAGE:
        #     sandbox.error = 'CPU limit exceeded'
        #     sandbox.container.kill()
        #     logger.warning(f'Killed sandbox {sandbox.id} because CPU limit exceeded')
        # elif sandbox.ram_usage > SANDBOX_MAX_RAM_USAGE:
        if sandbox.ram_usage > SANDBOX_MAX_RAM_USAGE:
            sandbox.evaluation_run.error = 'RAM limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because RAM limit exceeded')
        elif runtime > SANDBOX_MAX_RUNTIME:
            sandbox.evaluation_run.error = 'Runtime limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because runtime limit exceeded')
        
    def add_sandbox(self, evaluation_run: "EvaluationRun", src_dir: Path):
        sandbox = Sandbox(evaluation_run=evaluation_run, src_dir=src_dir, manager=self)
        self.sandboxes.append(sandbox)
        return sandbox

    async def wait_for_all_sandboxes(self):
        for sandbox in self.sandboxes:
            await sandbox.wait()
    
    def get_evaluation_runs(self) -> List["EvaluationRun"]:
        return [sandbox.evaluation_run for sandbox in self.sandboxes]
    
    def cleanup(self):
        try:
            self.proxy_container.remove(force=True)
        except Exception:
            pass
        
        for sandbox in self.sandboxes:
            sandbox.cleanup()
            self.sandboxes.remove(sandbox)

    def _start_proxy_container(self):
        """Start the nginx proxy container."""
        try:
            # Check if a container with the same name already exists and remove it
            try:
                existing_container = self.docker.containers.get(PROXY_CONTAINER_NAME)
                logger.info(f"Found existing proxy container {existing_container.id}, removing it")
                existing_container.remove(force=True)
            except docker.errors.NotFound:
                # Container doesn't exist, which is fine
                pass

            # Start container on the default bridge network so it can reach the host API
            container = self.docker.containers.run(
                image=PROXY_DOCKER_IMAGE,
                name=PROXY_CONTAINER_NAME,
                detach=True,
                environment={ "RIDGES_API_URL": RIDGES_API_URL }
            )

            # Also connect it to the internal sandbox network so sandboxes can reach it
            try:
                self.docker.networks.get(SANDBOX_NETWORK_NAME).connect(container)
                logger.info(f"Connected proxy container to {SANDBOX_NETWORK_NAME}")
            except Exception as net_e:
                logger.error(f"Failed to attach proxy container to internal network: {net_e}")

            # Allow some time for Nginx to become ready
            time.sleep(2)

            container.reload()
            if container.status != "running":
                raise RuntimeError(f"Proxy container is not running: {container.status}")

            return container
        except Exception as e:
            logger.error(f"Error starting proxy container: {e}")
            raise
