
import subprocess
import asyncio
from typing import Dict, Optional
from utils.logging_utils import get_logger
import docker
from new_validator.config import REPO_CACHE_DIR, REPOS_BASE_DIR
import docker
import subprocess
import shutil

logger = get_logger(__name__)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logger.warning("psutil not available - system metrics will return None")
    PSUTIL_AVAILABLE = False

async def get_system_metrics() -> Dict[str, Optional[float]]:
    """
    Collect system metrics from this validator/screener machine.
    
    Returns:
        Dict containing:
        - cpu_percent: CPU usage percentage (0-100)
        - ram_percent: RAM usage percentage (0-100)
        - ram_total_gb: Total RAM in GB
        - disk_percent: Disk usage percentage (0-100)
        - disk_total_gb: Total disk space in GB
        - containers: Number of Docker containers running
    """
    metrics = {
        "cpu_percent": None,
        "ram_percent": None,
        "ram_total_gb": None,
        "disk_percent": None,
        "disk_total_gb": None,
        "containers": None
    }
    
    if not PSUTIL_AVAILABLE:
        return metrics
        
    try:
        # Get CPU usage (non-blocking)
        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_percent"] = round(float(cpu_percent), 1)
        
        # Get RAM usage percentage and total
        memory = psutil.virtual_memory()
        metrics["ram_percent"] = round(float(memory.percent), 1)
        metrics["ram_total_gb"] = round(float(memory.total) / (1024**3), 1)  # Convert bytes to GB
        
        # Get disk usage percentage and total for root filesystem
        disk = psutil.disk_usage('/')
        metrics["disk_percent"] = round(float(disk.percent), 1)
        metrics["disk_total_gb"] = round(float(disk.total) / (1024**3), 1)  # Convert bytes to GB
        
        logger.debug(f"Collected psutil metrics: CPU={metrics['cpu_percent']}%, RAM={metrics['ram_percent']}% ({metrics['ram_total_gb']}GB total), Disk={metrics['disk_percent']}% ({metrics['disk_total_gb']}GB total)")
        
    except Exception as e:
        logger.warning(f"Error collecting psutil metrics: {e}")
    
    try:
        # Get Docker container count
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "ps", "-q"],
                capture_output=True,
                text=True,
                timeout=3
            )
        )
        
        if result.returncode == 0:
            # Count non-empty lines
            container_count = len([line for line in result.stdout.strip().split('\n') if line.strip()])
            metrics["containers"] = container_count
            logger.debug(f"Found {container_count} Docker containers")
        else:
            logger.warning(f"Docker ps failed with return code {result.returncode}: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        logger.warning("Docker ps command timed out")
    except FileNotFoundError:
        logger.warning("Docker command not found")
    except Exception as e:
        logger.warning(f"Error getting Docker container count: {e}")
    
    return metrics


def cleanup_docker_containers():
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
