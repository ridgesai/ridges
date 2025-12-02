import docker
import subprocess
import utils.logger as logger



docker_client = None

DOCKER_PREFIX = 'ridges-ai'


def _initialize_docker():
    logger.info("Initializing Docker...")
    try:
        global docker_client
        docker_client = docker.from_env()
        logger.info("Initialized Docker")
    except Exception as e:
        logger.fatal(f"Failed to initialize Docker: {e}")



def get_docker_client():
    """
    Gets the Docker client. If it is not initialized, initializes it (once per program).
    """

    if docker_client is None:
        _initialize_docker()
    
    return docker_client



def build_docker_image(dockerfile_dir: str, tag: str) -> None:
    """
    Builds a Docker image.

    Args:
        dockerfile_dir: Path to a directory containing a Dockerfile
        tag: Tag to give the Docker image
    """
    logger.info(f"Building Docker image: {tag}")
    
    try:
        result = subprocess.run(["docker", "build", "-t", tag, dockerfile_dir], text=True)
        
        if result.returncode == 0:
            logger.info(f"Successfully built Docker image: {tag}")
        else:
            raise Exception(f"Docker build failed with exit code {result.returncode}")
            
    except Exception as e:

        logger.error(f"Failed to build Docker image: {e}")
        raise



def get_num_docker_containers() -> int:
    """
    Gets the number of Docker containers running.
    """

    # This is equivalent to `docker ps -q | wc -l`
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=1)
    return len([line for line in result.stdout.strip().split('\n') if line.strip()])



# TODO ADAM: optimize
def stop_and_delete_all_docker_containers() -> None:
    """
    Stops and deletes all Docker containers.
    """

    docker_client = get_docker_client()
    
    logger.info("Stopping and deleting all containers with prefix {DOCKER_PREFIX}...")
    
    for container in docker_client.containers.list(all=True, filters={"name": f"^{DOCKER_PREFIX}"}):
        logger.info(f"Stopping and deleting container {container.name}...")

        try:
            container.stop(timeout=3)
        except Exception as e:
            logger.warning(f"Could not stop container {container.name}: {e}")
        
        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"Could not remove container {container.name}: {e}")

        logger.info(f"Stopped and deleted container {container.name}")

    docker_client.containers.prune()
    
    logger.info(f"Stopped and deleted all containers with prefix {DOCKER_PREFIX}")



def create_internal_docker_network(name: str) -> None:
    """
    Creates an internal Docker network, if it does not already exist.
    """

    prefixed_name = f"{DOCKER_PREFIX}-{name}"
    docker_client = get_docker_client()
    
    try:
        docker_client.networks.get(name)
        logger.info(f"Found internal Docker network: {name}")
    except docker.errors.NotFound:
        docker_client.networks.create(name, driver="bridge", internal=True)
        logger.info(f"Created internal Docker network: {name}")



def connect_docker_container_to_internet(container: docker.models.containers.Container) -> None:
    """
    Connects a Docker container to the internet.
    """

    docker_client = get_docker_client()

    logger.info(f"Connecting Docker container {container.name} to internet...")

    bridge_network = docker_client.networks.get("bridge")
    bridge_network.connect(container)
    
    logger.info(f"Connected Docker container {container.name} to internet")