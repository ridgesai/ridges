import asyncio
import docker
import subprocess
import shutil
from pathlib import Path

from dotenv import load_dotenv
load_dotenv("validator/.env")

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from validator.sandbox.constants import REPOS_BASE_DIR, REPO_CACHE_DIR
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

def cleanup_all_docker_containers():
    """
    Clean up ALL running Docker containers on validator startup.
    This ensures no orphaned containers from previous runs are left running.
    """
    logger.info("🧹 Starting aggressive Docker cleanup - removing ALL running containers")
    
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

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    # Clean up any orphaned Docker containers from previous runs
    cleanup_all_docker_containers()
    
    websocket_app = WebsocketApp()
    try:
        await websocket_app.start()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        await websocket_app.shutdown()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
