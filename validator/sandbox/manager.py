import os
import json
import shutil
import time
import asyncio
from typing import TYPE_CHECKING, List, Tuple, Optional
from pathlib import Path
from validator.utils.logging import get_logger
import docker
from docker import DockerClient
from docker.models.containers import Container

from validator.config import RIDGES_API_URL
from validator.sandbox.schema import EvaluationRun
from validator.sandbox.constants import (
    PROXY_CONTAINER_NAME,
    REPOS_BASE_DIR,
    SANDBOX_NETWORK_NAME,
    SANDBOX_MAX_RAM_USAGE,
    SANDBOX_MAX_RUNTIME,
    PROXY_DOCKER_IMAGE,
    AGENTS_BASE_DIR,
)

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp
    from validator.sandbox.sandbox import Sandbox

# Set up logger
logger = get_logger(__name__)


class SandboxManager:
    websocket_app: "WebsocketApp"
    proxy_container: Optional["Container"] = None
    docker: DockerClient
    sandboxes: List["Sandbox"]
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
            logger.info(
                f"Creating isolated docker network '{SANDBOX_NETWORK_NAME}' (internal=True)"
            )
            self.docker.networks.create(
                SANDBOX_NETWORK_NAME, driver="bridge", internal=True
            )

    async def _monitor(self):
        while True:
            for sandbox in self.sandboxes:
                if sandbox.running and sandbox.evaluation_run.error is None:
                    try:
                        # Allow cancellation
                        self._monitor_sandbox(sandbox)
                    except Exception:
                        continue

            await asyncio.sleep(1)

    def _monitor_sandbox(self, sandbox: "Sandbox"):
        # Get the stats
        stats = sandbox.container.stats(stream=False)

        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )

        sandbox.cpu_usage = cpu_delta / system_delta * 100
        sandbox.ram_usage = stats["memory_stats"]["usage"] / (1024 * 1024)

        current_time = time.time()
        runtime = current_time - sandbox.start_time

        # logger.debug(f'sandbox {sandbox.evaluation_run.run_id}: cpu: {sandbox.cpu_usage:.1f}, ram: {sandbox.ram_usage:.1f} MiB, runtime: {runtime:.1f} seconds')

        # Check if the sandbox is using too many resources, if so, kill it and set the error
        if sandbox.ram_usage > SANDBOX_MAX_RAM_USAGE:
            sandbox.evaluation_run.error = "RAM limit exceeded"
            sandbox.container.kill()
            logger.warning(
                f"Killed sandbox {sandbox.evaluation_run.run_id} because RAM limit exceeded"
            )
        elif runtime > SANDBOX_MAX_RUNTIME:
            sandbox.evaluation_run.error = "Runtime limit exceeded"
            sandbox.container.kill()
            logger.warning(
                f"Killed sandbox {sandbox.evaluation_run.run_id} because runtime limit exceeded"
            )

    def add_sandbox(self, evaluation_run: "EvaluationRun", agent_dir: Path):
        from validator.sandbox.sandbox import Sandbox

        sandbox = Sandbox(
            evaluation_run=evaluation_run, agent_dir=agent_dir, manager=self
        )
        self.sandboxes.append(sandbox)
        return sandbox

    async def wait_for_all_sandboxes(self):
        for sandbox in self.sandboxes:
            await sandbox.wait()

    def get_evaluation_runs(self) -> List["EvaluationRun"]:
        return [sandbox.evaluation_run for sandbox in self.sandboxes]

    def cleanup(self):
        logger.info("Starting SandboxManager cleanup")
        
        # Cancel the monitor task
        if hasattr(self, '_monitor_task') and self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                # Don't await here since we're in a sync method, just cancel
                pass
            except Exception as e:
                logger.warning(f"Error cancelling monitor task: {e}")

        # Cancel all sandbox tasks and stop containers
        for sandbox in self.sandboxes:
            try:
                # Cancel the sandbox task if it's running
                if hasattr(sandbox, '_task') and sandbox._task and not sandbox._task.done():
                    sandbox._task.cancel()
                
                # Stop and remove the container if it exists
                if hasattr(sandbox, 'container') and sandbox.container:
                    try:
                        sandbox.container.stop(timeout=5)
                        sandbox.container.remove()
                    except Exception as e:
                        logger.warning(f"Error stopping container for sandbox {sandbox.evaluation_run.run_id}: {e}")
                
                sandbox.running = False
            except Exception as e:
                logger.warning(f"Error cleaning up sandbox {getattr(sandbox, 'evaluation_run', {}).get('run_id', 'unknown')}: {e}")

        # Clean up ALL sandbox containers (including orphaned ones)
        self._cleanup_all_sandbox_containers()

        # Remove proxy container
        if self.proxy_container:
            try:
                self.proxy_container.remove(force=True)
            except Exception as e:
                logger.warning(f"Error removing proxy container: {e}")

        # Clean up filesystem
        try:
            shutil.rmtree(AGENTS_BASE_DIR, ignore_errors=True)
            shutil.rmtree(REPOS_BASE_DIR, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error cleaning up directories: {e}")

        # Clear sandbox list
        self.sandboxes.clear()
        logger.info("SandboxManager cleanup completed")

    def _cleanup_all_sandbox_containers(self):
        """Clean up ALL sandbox containers, including orphaned ones from previous runs."""
        try:
            # Find all containers with sandbox-runner image
            all_containers = self.docker.containers.list(all=True)
            sandbox_containers = [
                container for container in all_containers 
                if any(tag.startswith('sandbox-runner') for tag in container.image.tags)
            ]
            
            logger.info(f"Found {len(sandbox_containers)} sandbox containers to clean up")
            
            for container in sandbox_containers:
                try:
                    logger.info(f"Cleaning up sandbox container: {container.name} ({container.id[:12]})")
                    container.stop(timeout=5)
                    container.remove()
                except Exception as e:
                    logger.warning(f"Error cleaning up container {container.name}: {e}")
                    
        except Exception as e:
            logger.error(f"Error during comprehensive sandbox cleanup: {e}")

    def _start_proxy_container(self):
        """Start the nginx proxy container."""
        try:
            # Check if a container with the same name already exists and remove it
            try:
                existing_container = self.docker.containers.get(PROXY_CONTAINER_NAME)
                logger.info(
                    f"Found existing proxy container {existing_container.id}, removing it"
                )
                existing_container.remove(force=True)
            except docker.errors.NotFound:
                # Container doesn't exist, which is fine
                pass

            # Start container on the default bridge network so it can reach the host API
            container = self.docker.containers.run(
                image=PROXY_DOCKER_IMAGE,
                name=PROXY_CONTAINER_NAME,
                detach=True,
                environment={"RIDGES_API_URL": RIDGES_API_URL},
            )

            # Also connect it to the internal sandbox network so sandboxes can reach it
            try:
                self.docker.networks.get(SANDBOX_NETWORK_NAME).connect(container)
                logger.info(f"Connected proxy container to {SANDBOX_NETWORK_NAME}")
            except Exception as net_e:
                logger.error(
                    f"Failed to attach proxy container to internal network: {net_e}"
                )

            # Allow some time for Nginx to become ready
            time.sleep(2)

            container.reload()
            if container.status != "running":
                raise RuntimeError(
                    f"Proxy container is not running: {container.status}"
                )

            return container
        except Exception as e:
            logger.error(f"Error starting proxy container: {e}")
            raise
