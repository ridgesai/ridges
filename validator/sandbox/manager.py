import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from datetime import datetime, timezone

import docker
from docker.models.containers import Container
from ddtrace import tracer

from validator.config import RIDGES_PROXY_URL
from validator.sandbox.constants import (
    AGENTS_BASE_DIR,
    PROXY_CONTAINER_NAME,
    PROXY_DOCKER_IMAGE,
    REPOS_BASE_DIR,
    SANDBOX_NETWORK_NAME,
)
from validator.sandbox.schema import EvaluationRun, SwebenchProblem
from validator.sandbox.sandbox import Sandbox
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

class SandboxManager:
    """Manages sandbox orchestration and Docker infrastructure"""
    
    @tracer.wrap(resource="initialize-sandbox-manager")
    def __init__(self, websocket_app: "WebsocketApp"):
        self.websocket_app = websocket_app
        
        try:
            self.docker = docker.from_env(max_pool_size=100)
            self.docker.ping()
        except Exception:
            raise SystemExit("Docker isn't running. Please start Docker and try again.")
        
        self.sandboxes: List[Sandbox] = []
        self.proxy_container: Optional[Container] = None
        
        # Setup infrastructure
        self._setup_network()
        self._setup_proxy()
    
    @tracer.wrap(resource="setup-network-for-sandbox-manager")
    def _setup_network(self) -> None:
        """Setup Docker network and proxy container"""
        # Create network
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
        except docker.errors.NotFound:
            self.docker.networks.create(SANDBOX_NETWORK_NAME, driver="bridge", internal=True)
        
        # Remove existing proxy
        try:
            existing = self.docker.containers.get(PROXY_CONTAINER_NAME)
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass
    
    @tracer.wrap(resource="setup-proxy-for-sandbox-manager")
    def _setup_proxy(self) -> None:
        """Setup proxy container"""
        # Start proxy container
        try:
            self.proxy_container = self.docker.containers.run(
                image=PROXY_DOCKER_IMAGE,
                name=PROXY_CONTAINER_NAME,
                detach=True,
                environment={"RIDGES_PROXY_URL": RIDGES_PROXY_URL},
            )
        except docker.errors.ImageNotFound:
            raise SystemExit(f"No docker image for {PROXY_DOCKER_IMAGE}")
        except docker.errors.APIError as e:
            if "No such image" in str(e):
                raise SystemExit(f"No docker image for {PROXY_DOCKER_IMAGE}")
            raise
        
        # Connect to network
        network = self.docker.networks.get(SANDBOX_NETWORK_NAME)
        network.connect(self.proxy_container)
    
    @tracer.wrap(resource="create-sandbox")
    async def create_sandbox(self, evaluation_run: EvaluationRun, problem: SwebenchProblem, agent_dir: Path) -> Sandbox:
        """Create a new sandbox for evaluation"""
        sandbox = Sandbox(evaluation_run, problem, agent_dir, self)
        self.sandboxes.append(sandbox)
        
        # Update status to sandbox_created when created
        sandbox.evaluation_run.status = "sandbox_created"
        sandbox.evaluation_run.sandbox_created_at = datetime.now(timezone.utc)
        await sandbox._send_update()
        return sandbox
    
    @tracer.wrap(resource="run-all-sandboxes")
    async def run_all_sandboxes(self) -> None:
        """Run all sandboxes in parallel"""
        async def run_sandbox_with_error_handling(sandbox: Sandbox):
            """Run a single sandbox with error handling"""
            try:
                await sandbox.run()
            except Exception as e:
                logger.error(f"Sandbox {sandbox.evaluation_run.run_id} failed: {e}")
                sandbox.evaluation_run.error = str(e)
                sandbox.evaluation_run.status = "result_scored"
                sandbox.evaluation_run.solved = False
                await sandbox._send_update()
        
        # Create tasks for all sandboxes to run in parallel
        tasks = [
            asyncio.create_task(run_sandbox_with_error_handling(sandbox))
            for sandbox in self.sandboxes
        ]
        
        # Run all tasks concurrently
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    @tracer.wrap(resource="cleanup-sandbox-manager")
    def cleanup(self, force_cancel: bool = True) -> None:
        """Clean up sandbox resources"""
        # Cancel running sandboxes
        if force_cancel:
            for sandbox in self.sandboxes:
                if hasattr(sandbox, '_task') and sandbox._task and not sandbox._task.done():
                    sandbox._task.cancel()
        
        # Clean up sandboxes
        for sandbox in self.sandboxes:
            try:
                sandbox.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up sandbox: {e}")
        
        if force_cancel:
            # Remove sandbox containers
            try:
                containers = self.docker.containers.list(all=True)
                for container in containers:
                    try:
                        if (container.image and hasattr(container.image, 'tags') and 
                            container.image.tags and 
                            any(tag.startswith('sandbox-runner') for tag in container.image.tags)):
                            container.remove(force=True)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Error cleaning up containers: {e}")
            
            # Remove proxy container
            if self.proxy_container:
                try:
                    self.proxy_container.remove(force=True)
                except Exception:
                    pass
            
            # Clean up directories
            for path in [AGENTS_BASE_DIR, REPOS_BASE_DIR]:
                try:
                    if path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass
            
            self.sandboxes.clear()
        else:
            # Remove only completed sandboxes
            self.sandboxes = [s for s in self.sandboxes if hasattr(s, 'evaluation_run') and 
                             s.evaluation_run.status not in ["result_scored"]]
