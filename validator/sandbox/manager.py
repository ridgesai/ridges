import asyncio
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Set
from datetime import datetime, timezone

import docker
from docker.models.containers import Container
from docker.errors import NotFound, ImageNotFound, APIError
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


class TerminateTaskGroup(Exception):
    """Exception raised to terminate a task group."""

class SandboxManager:
    """Manages sandbox orchestration and Docker infrastructure"""
    
    def __init__(self, websocket_app: "WebsocketApp"):
        self.websocket_app = websocket_app
        self._container_ids: Set[str] = set()
        self._cleanup_attempted = False
        
        try:
            self.docker = docker.from_env(max_pool_size=100)
            self.docker.ping()
        except Exception:
            raise SystemExit("Docker isn't running. Please start Docker and try again.")
        
        self.sandboxes: List[Sandbox] = []
        self.proxy_container: Optional[Container] = None
        self._task_group: Optional[asyncio.TaskGroup] = None
        
        # Setup infrastructure
        self._setup_network()
        self._setup_proxy()
        self._setup_signal_handlers()

    @tracer.wrap(resource="setup-network-for-sandbox-manager")
    def _setup_network(self) -> None:
        """Setup Docker network and proxy container"""
        # Create network
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
        except NotFound:
            self.docker.networks.create(SANDBOX_NETWORK_NAME, driver="bridge", internal=True)
        
        # Remove existing proxy
        try:
            existing = self.docker.containers.get(PROXY_CONTAINER_NAME)
            existing.remove(force=True)
        except NotFound:
            pass
    
    @tracer.wrap(resource="setup-proxy-for-sandbox-manager")
    def _setup_proxy(self) -> None:
        """Setup proxy container"""
        try:
            self.proxy_container = self.docker.containers.run(
                image=PROXY_DOCKER_IMAGE,
                name=PROXY_CONTAINER_NAME,
                detach=True,
                environment={"RIDGES_PROXY_URL": RIDGES_PROXY_URL},
            )
        except ImageNotFound:
            raise SystemExit(f"No docker image for {PROXY_DOCKER_IMAGE}")
        except APIError as e:
            if "No such image" in str(e):
                raise SystemExit(f"No docker image for {PROXY_DOCKER_IMAGE}")
            raise
        
        # Connect to network
        network = self.docker.networks.get(SANDBOX_NETWORK_NAME)
        network.connect(self.proxy_container)
    
    def _setup_signal_handlers(self) -> None:
        def signal_handler(signum):
            logger.info(f"Received signal {signum}, performing sandbox cleanup")
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    def _track_container(self, container: Container) -> None:
        if container and hasattr(container, 'id'):
            self._container_ids.add(container.id)
            logger.debug(f"Tracking container {container.id}")
    
    def _untrack_container(self, container: Container) -> None:
        if container and hasattr(container, 'id'):
            self._container_ids.discard(container.id)
            logger.debug(f"Untracking container {container.id}")
    
    def _force_kill_container(self, container_id: str) -> bool:
        success = False
        
        try:
            container = self.docker.containers.get(container_id)
            container.kill()
            container.remove(force=True)
            success = True
            logger.info(f"Force killed container {container_id} via Docker API")
        except Exception as e:
            logger.warning(f"Failed to kill container {container_id} via Docker API: {e}")
        
        if not success:
            try:
                result = subprocess.run(
                    ["docker", "kill", container_id],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    subprocess.run(["docker", "rm", "-f", container_id], timeout=10)
                    success = True
                    logger.info(f"Force killed container {container_id} via docker CLI")
                else:
                    logger.warning(f"docker kill failed for {container_id}: {result.stderr}")
            except Exception as e:
                logger.error(f"docker CLI kill failed for {container_id}: {e}")
        
        return success
    
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
    
    def _force_terminate_task_group(self) -> None:
        """Used to force termination of a task group."""
        raise TerminateTaskGroup()
    
    def force_cancel_all_tasks(self) -> None:
        if self._cleanup_attempted:
            logger.debug("Cleanup already attempted, skipping")
            return
            
        self._cleanup_attempted = True
        logger.info("Force cancelling all sandbox tasks due to websocket disconnect")
        
        # First, mark all sandboxes as cancelled to stop websocket communications
        for sandbox in self.sandboxes:
            sandbox.cancel()
        
        containers_to_kill = list(self._container_ids)
        for container_id in containers_to_kill:
            self._force_kill_container(container_id)
        
        try:
            containers = self.docker.containers.list(all=True)
            for container in containers:
                try:
                    if (container.image and hasattr(container.image, 'tags') and 
                        container.image.tags and 
                        any(tag.startswith('sandbox-runner') for tag in container.image.tags)):
                        container.kill()
                        container.remove(force=True)
                        logger.info(f"Force killed missed sandbox container {container.id}")
                except Exception as e:
                    logger.warning(f"Failed to kill missed container {container.id}: {e}")
        except Exception as e:
            logger.error(f"Failed to list containers for cleanup: {e}")
        
        # Cancel task group
        if self._task_group is not None:
            try:
                # Access the internal task set to cancel all tasks immediately
                if hasattr(self._task_group, '_tasks'):
                    for task in list(self._task_group._tasks):
                        if not task.done():
                            task.cancel()
                
                # Also create a termination task as backup
                task = asyncio.create_task(self._force_terminate_task_group())
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except Exception as e:
                logger.error(f"Failed to cancel task group: {e}")
                # Fallback to original method if internal access fails
                task = asyncio.create_task(self._force_terminate_task_group())
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        self.sandbox_manager = None

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

        try:
            async with asyncio.TaskGroup() as tg:
                self._task_group = tg
                for sandbox in self.sandboxes:
                    tg.create_task(run_sandbox_with_error_handling(sandbox))
        except BaseExceptionGroup as eg:
            # Handle exception groups from TaskGroup (Python 3.11+ style)
            for exc in eg.exceptions:
                if isinstance(exc, TerminateTaskGroup):
                    logger.info("TaskGroup terminated by TerminateTaskGroup exception")
                elif isinstance(exc, asyncio.CancelledError):
                    logger.info("TaskGroup cancelled due to task cancellation")
                else:
                    logger.warning(f"TaskGroup exception: {exc}")
        except (TerminateTaskGroup, asyncio.CancelledError) as e:
            # Handle individual exceptions for compatibility
            logger.info(f"TaskGroup terminated: {type(e).__name__}")
        finally:
            self._task_group = None
    
    @tracer.wrap(resource="cleanup-sandbox-manager")
    def cleanup(self, force_cancel: bool = True) -> None:
        logger.info("Starting sandbox manager cleanup")
        
        # Terminate task group if running
        if force_cancel and self._task_group:
            try:
                self._task_group.create_task(self._force_terminate_task_group())
            except Exception as e:
                logger.error(f"Failed to terminate task group: {e}")
        
        # Clean up sandboxes
        for sandbox in self.sandboxes:
            try:
                sandbox.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up sandbox: {e}")
        
        for path in [AGENTS_BASE_DIR, REPOS_BASE_DIR]:
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info(f"Cleaned up directory: {path}")
            except Exception as e:
                logger.warning(f"Failed to clean up directory {path}: {e}")
        
        if force_cancel:
            containers_to_kill = list(self._container_ids)
            for container_id in containers_to_kill:
                self._force_kill_container(container_id)
            
            try:
                containers = self.docker.containers.list(all=True)
                for container in containers:
                    try:
                        if (container.image and hasattr(container.image, 'tags') and 
                            container.image.tags and 
                            any(tag.startswith('sandbox-runner') for tag in container.image.tags)):
                            container.remove(force=True)
                            logger.info(f"Removed sandbox container {container.id}")
                    except Exception as e:
                        logger.warning(f"Failed to remove container {container.id}: {e}")
                        try:
                            subprocess.run(["docker", "rm", "-f", container.id], timeout=10)
                            logger.info(f"Removed container {container.id} via CLI fallback")
                        except Exception as cli_error:
                            logger.error(f"CLI fallback also failed for container {container.id}: {cli_error}")
            except Exception as e:
                logger.warning(f"Error listing containers for cleanup: {e}")
            
            # Remove proxy container
            if self.proxy_container:
                try:
                    self.proxy_container.remove(force=True)
                    logger.info("Removed proxy container")
                except Exception as e:
                    logger.warning(f"Failed to remove proxy container: {e}")
                    try:
                        subprocess.run(["docker", "rm", "-f", PROXY_CONTAINER_NAME], timeout=10)
                        logger.info("Removed proxy container via CLI fallback")
                    except Exception as cli_error:
                        logger.error(f"CLI fallback also failed for proxy container: {cli_error}")
            
            self.sandboxes.clear()
            self._container_ids.clear()
        else:
            # Remove only completed sandboxes
            self.sandboxes = [s for s in self.sandboxes if hasattr(s, 'evaluation_run') and 
                             s.evaluation_run.status not in ["result_scored"]]
