import os
import json
import shutil
import time
import asyncio
from typing import TYPE_CHECKING, List, Tuple, Optional
from pathlib import Path
from shared.logging_utils import get_logger
import docker
from docker import DockerClient
from docker.models.containers import Container

from validator.config import RIDGES_API_URL
from validator.sandbox.sandbox import Sandbox
from validator.sandbox.schema import EvaluationRun

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
AGENTS_BASE_DIR = Path(__file__).parent.parent / "agents"
    
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
        if sandbox.ram_usage > SANDBOX_MAX_RAM_USAGE:
            sandbox.evaluation_run.error = 'RAM limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because RAM limit exceeded')
        elif runtime > SANDBOX_MAX_RUNTIME:
            sandbox.evaluation_run.error = 'Runtime limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because runtime limit exceeded')
        
    def add_sandbox(self, evaluation_run: "EvaluationRun", agent_dir: Path):
        sandbox = Sandbox(evaluation_run=evaluation_run, agent_dir=agent_dir, manager=self)
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

        shutil.rmtree(AGENTS_BASE_DIR, ignore_errors=True)
        shutil.rmtree(REPOS_BASE_DIR, ignore_errors=True)
        
        for sandbox in self.sandboxes:
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
