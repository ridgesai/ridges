"""Docker utility functions for container management.

This module provides low-level Docker operations used by the sandbox system.
It handles container lifecycle, image building, network management, and cleanup.

Key Design Decisions:
---------------------
- Lazy initialization: Docker client is created on first use to handle startup errors gracefully
- Prefix-based filtering: All containers are tagged with prefixes for easy identification and cleanup
- Subprocess for builds: Docker SDK has limitations with build output streaming, so we use subprocess
- Graceful degradation: Errors during cleanup are logged but don't prevent continued operation
"""

import docker
import subprocess
import utils.logger as logger



# Prefixes used to identify and filter containers managed by this system
# DOCKER_PREFIX: Standard ridges-ai containers (sandboxes, proxy)
# SWEBENCH_DOCKER_PREFIX: Containers from SWE-bench evaluation framework
DOCKER_PREFIX = 'ridges-ai'
SWEBENCH_DOCKER_PREFIX = 'sweb'



# Global Docker client instance - initialized lazily via get_docker_client()
docker_client = None



def _initialize_docker():
    """Initialize the global Docker client from environment configuration.
    
    Attempts to create a Docker client using environment variables (DOCKER_HOST, etc.)
    or falls back to the default local Docker socket.
    
    Raises:
        System exit if Docker daemon is unreachable or not properly configured
    """
    logger.info("Initializing Docker...")
    try:
        global docker_client
        docker_client = docker.from_env()
        logger.info("Initialized Docker")
    except Exception as e:
        logger.fatal(f"Failed to initialize Docker: {e}")



def get_docker_client():
    """Get the global Docker client, initializing if necessary.
    
    Returns:
        docker.DockerClient: The initialized Docker client instance
    """
    if docker_client is None:
        _initialize_docker()
    
    return docker_client



def build_docker_image(dockerfile_dir: str, tag: str) -> None:
    """Build a Docker image from a Dockerfile.
    
    Uses subprocess instead of the Docker SDK to better handle build output
    streaming and error reporting.
    
    Args:
        dockerfile_dir: Directory containing the Dockerfile and build context
        tag: Base image tag (will be prefixed with DOCKER_PREFIX)
        
    Raises:
        subprocess.CalledProcessError: If the Docker build fails
    """
    tag = f"{DOCKER_PREFIX}-{tag}"
    logger.info(f"Building Docker image: {tag}")
    subprocess.run(["docker", "build", "-t", tag, dockerfile_dir], text=True, check=True)
    logger.info(f"Successfully built Docker image: {tag}")



def get_num_docker_containers() -> int:
    """Count the number of running Docker containers.
    
    Uses subprocess to quickly count running containers without loading
    full container objects into memory.
    
    Returns:
        int: Number of running containers (timeout returns 0)
    """
    # This is equivalent to `docker ps -q | wc -l`
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=1)
    return len([line for line in result.stdout.strip().split('\n') if line.strip()])



# TODO ADAM: optimize
def stop_and_delete_all_docker_containers() -> None:
    """Stop and remove all containers managed by this system.
    
    This cleans up any leftover containers from previous runs, ensuring a clean state.
    It targets containers with names matching DOCKER_PREFIX or SWEBENCH_DOCKER_PREFIX.
    
    Error Handling:
        - Stop/remove failures are logged but don't stop the cleanup process
        - Individual container failures don't prevent cleanup of other containers
        - Container prune is called at the end to remove any orphaned resources
    
    TODO: Optimize by using parallel operations or batch API calls
    """
    docker_client = get_docker_client()
    
    logger.info(f"Stopping and deleting all containers...")
    
    # Filter containers by name prefix to only target our managed containers
    for container in docker_client.containers.list(all=True, filters={"name": f"^({DOCKER_PREFIX}|{SWEBENCH_DOCKER_PREFIX})"}):
        logger.info(f"Stopping and deleting container {container.name}...")

        # Attempt graceful stop first (3 second timeout)
        try:
            container.stop(timeout=3)
        except Exception as e:
            logger.warning(f"Failed to stop container {container.name}: {e}")
            # Continue to force removal even if stop fails
        
        # Force remove the container
        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"Failed to remove container {container.name}: {e}")
            continue

        logger.info(f"Stopped and deleted container {container.name}")

    # Prune any remaining stopped containers and reclaim resources
    docker_client.containers.prune()
    
    logger.info(f"Stopped and deleted all containers")



def create_internal_docker_network(name: str) -> None:
    """Create an internal Docker network for sandbox isolation.
    
    Internal networks prevent containers from accessing the external internet,
    creating a secure sandbox environment. Containers on this network can only
    communicate with each other and the host.
    
    Args:
        name: Name for the Docker network
        
    Note:
        If the network already exists, this function is a no-op (idempotent)
    """
    docker_client = get_docker_client()
    
    try:
        # Check if network already exists
        docker_client.networks.get(name)
        logger.info(f"Found internal Docker network: {name}")
    except docker.errors.NotFound:
        # Create an internal network - containers on this network cannot access the internet
        docker_client.networks.create(name, driver="bridge", internal=True)
        logger.info(f"Created internal Docker network: {name}")



def connect_docker_container_to_internet(container: docker.models.containers.Container) -> None:
    """Connect a container to the default bridge network for internet access.
    
    This is used specifically for the proxy container, which needs internet access
    to forward requests from isolated sandboxes to the inference gateway.
    
    The default bridge network provides NAT-based internet access to containers.
    
    Args:
        container: The Docker container to connect to the internet
        
    Security Note:
        This should ONLY be called for the proxy container. Sandboxes must remain
        on the internal network only to maintain isolation.
    """
    docker_client = get_docker_client()

    logger.info(f"Connecting Docker container {container.name} to internet...")

    # Connect to the default bridge network which has internet access
    bridge_network = docker_client.networks.get("bridge")
    bridge_network.connect(container)
    
    logger.info(f"Connected Docker container {container.name} to internet")