import docker
import subprocess
import utils.logger as logger


logger.info("Creating Docker client...")
try:
    docker_client = docker.from_env()
    logger.info("Created Docker client")
except Exception as e:
    logger.fatal(f"Failed to create Docker client: {e}")



def build_docker_image(dockerfile_dir, tag):
    """
    Build a Docker image.

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



def get_num_docker_containers():
    """
    Get the number of Docker containers running.
    """

    # This is equivalent to `docker ps -q | wc -l`
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=1)
    return len([line for line in result.stdout.strip().split('\n') if line.strip()])



# TODO ADAM
def stop_and_delete_all_docker_containers():
    """
    Stop and delete all Docker containers.
    """
    
    logger.info("Stopping and deleting all containers...")
    
    for container in docker_client.containers.list(all=True):
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
    
    logger.info("Stopped and deleted all containers")



def create_internal_docker_network(name: str):
    """
    Creates an internal Docker network, if it does not already exist.
    """
    
    try:
        docker_client.networks.get(name)
        logger.info(f"Found internal Docker network: {name}")
    except docker.errors.NotFound:
        docker_client.networks.create(name, driver="bridge", internal=True)
        logger.info(f"Created internal Docker network: {name}")



def connect_docker_container_to_internet(container: docker.models.containers.Container):
    """
    Connects a Docker container to the internet.
    """

    logger.info(f"Connecting Docker container {container.name} to internet...")

    bridge_network = docker_client.networks.get("bridge")
    bridge_network.connect(container)
    
    logger.info(f"Connected Docker container {container.name} to internet")