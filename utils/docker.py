import re
import subprocess

import docker

import utils.logger as logger

DOCKER_PREFIX = "ridges-ai"
SWEBENCH_DOCKER_PREFIX = "sweb"
HARBOR_CONTAINER_NAME_PATTERN = re.compile(r".+__.+-(main|sandbox-proxy)-1$")
HARBOR_NETWORK_NAME_PATTERN = re.compile(r".+__.+_sandbox_(internal|egress)$")


docker_client = None


def _initialize_docker():
    logger.info("Initializing Docker...")
    try:
        global docker_client
        docker_client = docker.from_env()
        logger.info("Initialized Docker")
    except Exception as e:
        logger.fatal(f"Failed to initialize Docker: {e}")


def get_docker_client():
    if docker_client is None:
        _initialize_docker()

    return docker_client


def build_docker_image(dockerfile_dir: str, tag: str) -> None:
    tag = f"{DOCKER_PREFIX}-{tag}"
    logger.info(f"Building Docker image: {tag}")
    subprocess.run(["docker", "build", "-t", tag, dockerfile_dir], text=True, check=True)
    logger.info(f"Successfully built Docker image: {tag}")


def get_num_docker_containers() -> int:
    # This is equivalent to `docker ps -q | wc -l`
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=1)
    return len([line for line in result.stdout.strip().split("\n") if line.strip()])


# TODO ADAM: optimize
def stop_and_delete_all_docker_containers() -> None:
    docker_client = get_docker_client()

    logger.info("Stopping and deleting all containers...")

    for container in docker_client.containers.list(
        all=True, filters={"name": f"^({DOCKER_PREFIX}|{SWEBENCH_DOCKER_PREFIX})"}
    ):
        logger.info(f"Stopping and deleting container {container.name}...")

        try:
            container.stop(timeout=3)
        except Exception as e:
            logger.warning(f"Failed to stop container {container.name}: {e}")
            # continue

        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"Failed to remove container {container.name}: {e}")
            continue

        logger.info(f"Stopped and deleted container {container.name}")

    docker_client.containers.prune()

    logger.info("Stopped and deleted all containers")


def cleanup_harbor_docker_resources() -> None:
    docker_client = get_docker_client()

    logger.info("Cleaning up stale Harbor Docker containers...")

    removed_containers = 0
    for container in docker_client.containers.list(all=True):
        if not HARBOR_CONTAINER_NAME_PATTERN.fullmatch(container.name):
            continue

        logger.info(f"Removing stale Harbor container {container.name}...")
        try:
            container.remove(force=True)
            removed_containers += 1
        except Exception as e:
            logger.warning(f"Failed to remove stale Harbor container {container.name}: {e}")

    logger.info(f"Removed {removed_containers} stale Harbor container(s)")
    logger.info("Cleaning up stale Harbor Docker networks...")

    removed_networks = 0
    for network in docker_client.networks.list():
        if network.name in ("bridge", "host", "none"):
            continue
        if not HARBOR_NETWORK_NAME_PATTERN.fullmatch(network.name):
            continue

        logger.info(f"Removing stale Harbor network {network.name}...")
        try:
            network.remove()
            removed_networks += 1
        except Exception as e:
            logger.warning(f"Failed to remove stale Harbor network {network.name}: {e}")

    logger.info(f"Removed {removed_networks} stale Harbor network(s)")
    logger.info("Finished cleaning up stale Harbor Docker resources")


def prune_docker_disk_resources(*, include_build_cache: bool = False) -> None:
    docker_client = get_docker_client()

    logger.info("Pruning dangling Docker images...")
    try:
        result = docker_client.images.prune(filters={"dangling": True, "until": "6h"})
        logger.info(f"Reclaimed {result.get('SpaceReclaimed', 0)} byte(s) from dangling Docker images")
    except Exception as e:
        logger.warning(f"Failed to prune dangling Docker images: {e}")

    if not include_build_cache:
        return

    logger.info("Pruning Docker build cache...")
    try:
        result = docker_client.api.prune_builds()
        logger.info(f"Reclaimed {result.get('SpaceReclaimed', 0)} byte(s) from Docker build cache")
    except Exception as e:
        logger.warning(f"Failed to prune Docker build cache: {e}")


def create_internal_docker_network(name: str) -> None:
    docker_client = get_docker_client()

    try:
        docker_client.networks.get(name)
        logger.info(f"Found internal Docker network: {name}")
    except docker.errors.NotFound:
        docker_client.networks.create(name, driver="bridge", internal=True)
        logger.info(f"Created internal Docker network: {name}")


def connect_docker_container_to_internet(container: docker.models.containers.Container) -> None:
    docker_client = get_docker_client()

    logger.info(f"Connecting Docker container {container.name} to internet...")

    bridge_network = docker_client.networks.get("bridge")
    bridge_network.connect(container)

    logger.info(f"Connected Docker container {container.name} to internet")
