import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import docker
from docker.models.containers import Container

from validator.config import RIDGES_API_URL
from validator.sandbox.constants import (
    AGENTS_BASE_DIR,
    PROXY_CONTAINER_NAME,
    PROXY_DOCKER_IMAGE,
    REPOS_BASE_DIR,
    SANDBOX_NETWORK_NAME,
)
from validator.sandbox.schema import EvaluationRun, SandboxState
from validator.sandbox.sandbox import Sandbox
from validator.utils.logging import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

class SandboxManager:
    """Manages sandbox orchestration and Docker infrastructure"""
    
    def __init__(self, websocket_app: "WebsocketApp"):
        self.websocket_app = websocket_app
        self.docker = docker.from_env(max_pool_size=100)
        self.sandboxes: List[Sandbox] = []
        self.proxy_container: Optional[Container] = None
        
        # Initialize infrastructure
        self._setup_infrastructure()
    
    def _setup_infrastructure(self) -> None:
        """Setup Docker network and proxy container"""
        self._create_network()
        self.proxy_container = self._start_proxy_container()
        logger.info("Sandbox infrastructure initialized")
    
    def _create_network(self) -> None:
        """Create isolated Docker network for sandboxes"""
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
            logger.info(f"Using existing network: {SANDBOX_NETWORK_NAME}")
        except docker.errors.NotFound:
            logger.info(f"Creating isolated network: {SANDBOX_NETWORK_NAME}")
            self.docker.networks.create(
                SANDBOX_NETWORK_NAME, 
                driver="bridge", 
                internal=True
            )
    
    def _start_proxy_container(self) -> Container:
        """Start nginx proxy container for AI API access"""
        # Remove any existing proxy container
        try:
            existing = self.docker.containers.get(PROXY_CONTAINER_NAME)
            logger.info(f"Removing existing proxy container: {existing.id[:12]}")
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass
        
        # Start new proxy container
        container = self.docker.containers.run(
            image=PROXY_DOCKER_IMAGE,
            name=PROXY_CONTAINER_NAME,
            detach=True,
            environment={"RIDGES_API_URL": RIDGES_API_URL},
        )
        
        # Connect to sandbox network
        try:
            network = self.docker.networks.get(SANDBOX_NETWORK_NAME)
            network.connect(container)
            logger.info(f"Connected proxy to {SANDBOX_NETWORK_NAME}")
        except Exception as e:
            logger.error(f"Failed to connect proxy to network: {e}")
            raise
        
        return container
    
    def create_sandbox(self, evaluation_run: EvaluationRun, agent_dir: Path) -> Sandbox:
        """Create a new sandbox for evaluation"""
        try:
            sandbox = Sandbox(evaluation_run, agent_dir, self)
            self.sandboxes.append(sandbox)
            logger.info(f"Created sandbox for run {evaluation_run.run_id}")
            return sandbox
        except Exception as e:
            logger.error(f"Failed to create sandbox: {e}")
            raise
    
    async def run_sandbox(self, sandbox: Sandbox, evaluation_run_data: dict) -> None:
        """Run a sandbox evaluation"""
        try:
            sandbox._task = asyncio.create_task(sandbox.run(evaluation_run_data))
            await sandbox._task
        except Exception as e:
            logger.error(f"Sandbox {sandbox.evaluation_run.run_id} failed: {e}")
            raise
    
    async def wait_for_all_sandboxes(self) -> None:
        """Wait for all sandboxes to complete"""
        if not self.sandboxes:
            return
        
        tasks = [
            sandbox._task for sandbox in self.sandboxes 
            if sandbox._task and not sandbox._task.done()
        ]
        
        if tasks:
            logger.info(f"Waiting for {len(tasks)} sandbox(es) to complete")
            await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_evaluation_runs(self) -> List[EvaluationRun]:
        """Get all evaluation runs"""
        return [sandbox.evaluation_run for sandbox in self.sandboxes]
    
    def get_running_sandboxes(self) -> List[Sandbox]:
        """Get sandboxes that are currently running"""
        return [
            sandbox for sandbox in self.sandboxes 
            if sandbox.state not in [SandboxState.COMPLETED, SandboxState.FAILED, SandboxState.CANCELLED]
        ]
    
    async def cancel_all_sandboxes(self) -> None:
        """Cancel all running sandboxes"""
        running = self.get_running_sandboxes()
        if not running:
            return
        
        logger.info(f"Cancelling {len(running)} running sandbox(es)")
        await asyncio.gather(*[sandbox.cancel() for sandbox in running], return_exceptions=True)
    
    def cleanup(self, force_cancel: bool = True) -> None:
        """Clean up sandbox resources"""
        logger.info(f"Starting cleanup (force_cancel={force_cancel})")
        
        # Cancel running sandboxes if requested
        if force_cancel:
            try:
                # Can't use async in sync method, so we'll just cancel tasks
                for sandbox in self.get_running_sandboxes():
                    if sandbox._task and not sandbox._task.done():
                        sandbox._task.cancel()
            except Exception as e:
                logger.warning(f"Error cancelling sandbox tasks: {e}")
        
        # Clean up individual sandboxes
        for sandbox in self.sandboxes:
            try:
                sandbox.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up sandbox {sandbox.evaluation_run.run_id}: {e}")
        
        # Clean up Docker containers
        if force_cancel:
            self._cleanup_docker_containers()
            
            # Remove proxy container
            if self.proxy_container:
                try:
                    self.proxy_container.remove(force=True)
                    logger.info("Removed proxy container")
                except Exception as e:
                    logger.warning(f"Error removing proxy container: {e}")
            
            # Clean up filesystem
            self._cleanup_filesystem()
            
            # Clear sandbox list
            self.sandboxes.clear()
        else:
            # Remove only completed sandboxes
            self.sandboxes = [s for s in self.sandboxes if s.state not in [
                SandboxState.COMPLETED, SandboxState.FAILED, SandboxState.CANCELLED
            ]]
        
        logger.info("Cleanup completed")
    
    def _cleanup_docker_containers(self) -> None:
        """Clean up all sandbox-related Docker containers"""
        try:
            containers = self.docker.containers.list(all=True)
            sandbox_containers = []
            
            for container in containers:
                try:
                    # Check if container has sandbox-runner image
                    if container.image and hasattr(container.image, 'tags') and container.image.tags:
                        if any(tag.startswith('sandbox-runner') for tag in container.image.tags):
                            sandbox_containers.append(container)
                    # Also check container name patterns as fallback
                    elif container.name and ('sandbox' in container.name.lower() or 'runner' in container.name.lower()):
                        sandbox_containers.append(container)
                except Exception as e:
                    logger.warning(f"Error checking container {container.id[:12]}: {e}")
                    continue
            
            logger.info(f"Found {len(sandbox_containers)} sandbox containers to remove")
            
            for container in sandbox_containers:
                try:
                    container.remove(force=True)
                    logger.info(f"Removed container: {container.name}")
                except Exception as e:
                    logger.warning(f"Error removing container {container.name}: {e}")
        except Exception as e:
            logger.error(f"Error during container cleanup: {e}")
    
    def _cleanup_filesystem(self) -> None:
        """Clean up sandbox directories"""
        for path in [AGENTS_BASE_DIR, REPOS_BASE_DIR]:
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info(f"Removed directory: {path}")
            except Exception as e:
                logger.warning(f"Error removing directory {path}: {e}")
