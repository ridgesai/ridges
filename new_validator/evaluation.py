from new_validator.actions.evals.constants import REPO_CACHE_DIR, REPOS_BASE_DIR
from new_validator.actions.resource_management.system_metrics import get_system_metrics
from new_validator.connection import ConnectionManager

import asyncio
import docker
import subprocess
import shutil

from typing import Optional
import docker

from logging import getLogger

from new_validator.utils.messaging import Heartbeat

logger = getLogger(__name__)

class EvaluationManager():
    connection_manager: ConnectionManager
    evaluation_task: Optional[asyncio.Task]
    heartbeat_task: Optional[asyncio.Task]

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self.connection_manager = connection_manager

        # Begin by cleanup of docker containers
        self._cleanup_docker_containers()

        # Start heartbeat task
        self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
        


    def _cleanup_docker_containers():
        """
        Cleanup all running Docker containers on validator startup to avoid orphaned evaluations.
        """

        try:
            docker_client = docker.from_env()
            docker_client.ping()
            
            # Get all containers (running and stopped)
            containers = docker_client.containers.list(all=True)
            
            if not containers:
                logger.info("✅ No Docker containers found to clean up")
                return
            
            logger.info(f"🔍 Found {len(containers)} Docker containers to remove")
            
            # Kill and remove all containers
            removed_count = 0
            for container in containers:
                try:
                    container_info = f"{container.id[:12]} ({container.name})"
                    logger.info(f"🗑️  Removing container: {container_info}")
                    
                    # Kill if running, then remove
                    if container.status == 'running':
                        container.kill()
                    container.remove(force=True)
                    removed_count += 1
                    
                except Exception as e:
                    logger.warning(f"⚠️  Failed to remove container {container.id[:12]}: {e}")
                    # Fallback to CLI
                    try:
                        subprocess.run(["docker", "rm", "-f", container.id], 
                                    capture_output=True, timeout=10)
                        logger.info(f"✅ Removed {container.id[:12]} via CLI fallback")
                        removed_count += 1
                    except Exception as cli_error:
                        logger.error(f"❌ CLI fallback also failed for {container.id[:12]}: {cli_error}")
            
            logger.info(f"🎯 Docker cleanup complete: {removed_count}/{len(containers)} containers removed")
            
            # Also clean up any orphaned networks
            try:
                networks = docker_client.networks.list()
                for network in networks:
                    # Skip default networks
                    if network.name not in ['bridge', 'host', 'none']:
                        try:
                            network.remove()
                            logger.info(f"🌐 Removed orphaned network: {network.name}")
                        except Exception as e:
                            logger.debug(f"Network {network.name} couldn't be removed (may be in use): {e}")
            except Exception as e:
                logger.warning(f"Failed to clean up networks: {e}")
                
        except docker.errors.DockerException as e:
            logger.error(f"❌ Docker not available for cleanup: {e}")
            logger.error("⚠️  Make sure Docker is running!")
        except Exception as e:
            logger.error(f"❌ Unexpected error during Docker cleanup: {e}")
        
        # Clean up Docker system (logs, build cache, unused images, etc.)
        try:
            logger.info("🧹 Running Docker system cleanup to remove logs and cache...")
            
            # First, prune the system to remove unused containers, networks, images, and build cache
            result = subprocess.run(
                ["docker", "system", "prune", "-a", "-f", "--volumes"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                logger.info("✅ Docker system prune completed successfully")
                if result.stdout:
                    logger.info(f"Docker system prune output: {result.stdout.strip()}")
            else:
                logger.warning(f"⚠️  Docker system prune had issues: {result.stderr}")
            
            # Also specifically prune builder cache which can be huge
            subprocess.run(
                ["docker", "builder", "prune", "-a", "-f"],
                capture_output=True, text=True, timeout=60
            )
            logger.info("✅ Docker builder cache pruned")
            
        except subprocess.TimeoutExpired:
            logger.warning("⚠️  Docker system cleanup timed out - continuing anyway")
        except Exception as e:
            logger.warning(f"⚠️  Failed to run Docker system cleanup: {e}")
        
        # Clean up repos and cache directories
        try:
            directories_to_clean = [
                (REPOS_BASE_DIR, "repos directory"),
                (REPO_CACHE_DIR, "repo cache directory")
            ]
            
            for directory, description in directories_to_clean:
                if directory.exists():
                    logger.info(f"🗑️  Wiping {description}: {directory}")
                    shutil.rmtree(directory, ignore_errors=True)
                    logger.info(f"✅ {description.capitalize()} cleaned up successfully")
                else:
                    logger.info(f"📁 {description.capitalize()} doesn't exist, nothing to clean")
        except Exception as e:
            logger.warning(f"⚠️  Failed to clean up directories: {e}") 

        
    async def _send_heartbeat(self):
        """Send periodic heartbeat messages with system metrics to the platform."""
        while self.connection_manager.ws:
            await asyncio.sleep(2.5)
            
            status = "available"
            if self.evaluation_task is not None and not self.evaluation_task.done() and not self.evaluation_task.cancelled():
                status = "evaluating"

            # Collect system metrics
            try:
                logger.debug("Collecting system metrics...")
                system_metrics = await get_system_metrics()
                logger.debug(f"Raw system metrics collected: {system_metrics}")
                
                # Only include metrics that aren't None
                metrics_to_send = {k: v for k, v in system_metrics.items() if v is not None}
                logger.debug(f"Non-null metrics to send: {metrics_to_send}")
                
                heartbeat = Heartbeat(
                    status=status,
                    **metrics_to_send 
                )

                if metrics_to_send:
                    logger.info(f"📊 Sending heartbeat WITH metrics: {metrics_to_send}")
                else:
                    logger.warning("📊 Sending heartbeat WITHOUT metrics (all None or psutil unavailable)")

                await self.connection_manager.send(message=heartbeat)

            except Exception as e:
                logger.error(f"❌ Failed to collect system metrics, sending heartbeat without them: {e}")
                # Fallback to heartbeat without metrics
                await self.send({"event": "heartbeat", "status": status})


    def handle_evaluation_request():
        pass 

    def shutdown_evaluations():
        pass
    