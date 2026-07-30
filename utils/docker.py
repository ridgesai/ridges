import logging
import os
import re
import subprocess
from datetime import datetime, timezone

import docker

logger = logging.getLogger(__name__)

DOCKER_PREFIX = "ridges-ai"
SWEBENCH_DOCKER_PREFIX = "sweb"
HARBOR_CONTAINER_NAME_PATTERN = re.compile(r".+__.+-(main|sandbox-proxy)-1$")
HARBOR_NETWORK_NAME_PATTERN = re.compile(r".+__.+_sandbox_(internal|egress)$")
HARBOR_IMAGE_REPO_PATTERN = re.compile(r".+__.+__.+-(main|env-main)$")
RIDGES_TRIAL_ID_LABEL = "ridges.trial_id"


docker_client = None
docker_client_long_timeout = None


def get_prune_timeout_seconds() -> int:
    """Client timeout for prune operations, which can run for minutes on a
    backlogged daemon (docker-py's 60s default would abandon them mid-flight).
    """
    try:
        return max(60, int(os.getenv("CLEANUP_PRUNE_TIMEOUT_SECONDS", "1800")))
    except ValueError:
        return 1800


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


def get_long_timeout_docker_client():
    """A docker client for slow bulk operations (prunes); lazily initialised."""

    global docker_client_long_timeout
    if docker_client_long_timeout is None:
        try:
            docker_client_long_timeout = docker.from_env(timeout=get_prune_timeout_seconds())
        except Exception as e:
            logger.warning(f"Failed to initialize long-timeout Docker client, falling back to default: {e}")
            return get_docker_client()

    return docker_client_long_timeout


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
    docker_client = get_long_timeout_docker_client()

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


def _parse_docker_time(value) -> datetime | None:
    """Parse a docker RFC3339Nano timestamp into aware-UTC, or None if unusable.

    Zero values ("0001-01-01T00:00:00Z") mean "never set" and return None.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        match = re.match(r"^(.*?\.\d{1,6})\d*([+-]\d{2}:\d{2})$", text)
        if match:
            text = match.group(1) + match.group(2)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if parsed.year <= 1:
            return None
        return parsed
    except ValueError:
        return None


def _age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    age = (now - timestamp).total_seconds()
    return age if age >= 0 else None


def sweep_stale_harbor_containers(
    *,
    stopped_grace_sec: float,
    running_ttl_sec: float,
    dry_run: bool,
) -> int:
    """
    Remove leaked Harbor trial containers (and then detached Harbor networks).
    Returns the number of containers removed (or that would be removed in dry-run mode).
    """
    docker_client = get_docker_client()
    now = datetime.now(timezone.utc)
    removed = 0

    try:
        summaries = docker_client.api.containers(all=True, filters={"label": RIDGES_TRIAL_ID_LABEL})
    except Exception as e:
        logger.warning(f"Janitor: failed to list containers: {e}")
        return 0

    for summary in summaries:
        names = [n.lstrip("/") for n in (summary.get("Names") or [])]
        name = names[0] if names else (summary.get("Id") or "")[:12]
        try:
            if not any(HARBOR_CONTAINER_NAME_PATTERN.fullmatch(n) for n in names):
                continue

            container = docker_client.containers.get(summary.get("Id") or name)
            name = container.name
            if not HARBOR_CONTAINER_NAME_PATTERN.fullmatch(container.name):
                continue

            def _qualifies(c) -> bool:
                age = _age_seconds(_parse_docker_time(c.attrs.get("Created")), now)
                if age is None:
                    return False
                if c.status in ("exited", "created", "dead"):
                    return age > stopped_grace_sec
                if c.status == "running":
                    return age > running_ttl_sec
                return False

            if not _qualifies(container):
                continue

            container.reload()
            if not _qualifies(container):
                continue

            if dry_run:
                logger.info(
                    f"Janitor (dry-run): would remove container {container.name} "
                    f"(status={container.status}, created={container.attrs.get('Created')})"
                )
            else:
                container.remove(force=container.status == "running")
                logger.info(f"Janitor: removed leaked container {container.name} (status={container.status})")
            removed += 1
        except docker.errors.NotFound:
            continue
        except Exception as e:
            logger.warning(f"Janitor: failed to remove container {name}: {e}")

    try:
        networks = docker_client.networks.list()
    except Exception as e:
        logger.warning(f"Janitor: failed to list networks: {e}")
        return removed

    for network in networks:
        try:
            if network.name in ("bridge", "host", "none"):
                continue
            if not HARBOR_NETWORK_NAME_PATTERN.fullmatch(network.name):
                continue
            age = _age_seconds(_parse_docker_time(network.attrs.get("Created")), now)
            if age is None or age <= stopped_grace_sec:
                continue
            network.reload()
            if network.attrs.get("Containers"):
                continue
            compose_network = (network.attrs.get("Labels") or {}).get("com.docker.compose.network")
            if compose_network not in ("sandbox_internal", "sandbox_egress"):
                continue
            if dry_run:
                logger.info(f"Janitor (dry-run): would remove network {network.name}")
            else:
                network.remove()
                logger.info(f"Janitor: removed leaked network {network.name}")
        except docker.errors.NotFound:
            continue
        except Exception as e:
            logger.warning(f"Janitor: failed to remove network {network.name}: {e}")

    return removed


def sweep_leaked_harbor_images(*, tag_grace_sec: float, dry_run: bool) -> int:
    """
    Untag leaked Harbor trial env images (unique per trial, never reused).
    Returns the number of image tags removed (or that would be removed in dry-run mode).
    """
    docker_client = get_docker_client()
    now = datetime.now(timezone.utc)
    removed = 0

    try:
        container_summaries = docker_client.api.containers(all=True)
        image_summaries = docker_client.api.images()
    except Exception as e:
        logger.warning(f"Janitor: failed to list images/containers: {e}")
        return 0

    referenced_image_ids = set()
    for summary in container_summaries:
        for key in ("ImageID", "Image"):
            value = summary.get(key)
            if value:
                referenced_image_ids.add(value)

    for summary in image_summaries:
        image_id = summary.get("Id") or ""
        try:
            harbor_tags = [
                tag
                for tag in (summary.get("RepoTags") or [])
                if HARBOR_IMAGE_REPO_PATTERN.fullmatch(tag.rsplit(":", 1)[0])
            ]
            if not harbor_tags:
                continue
            if image_id in referenced_image_ids:
                continue

            image = docker_client.images.get(image_id)
            last_tag_time = _parse_docker_time((image.attrs.get("Metadata") or {}).get("LastTagTime"))
            age = _age_seconds(last_tag_time, now)
            if age is None or age <= tag_grace_sec:
                continue

            size_mb = (image.attrs.get("Size") or 0) / 1e6
            for tag in harbor_tags:
                if dry_run:
                    logger.info(f"Janitor (dry-run): would untag leaked image {tag} ({size_mb:.0f} MB)")
                else:
                    docker_client.images.remove(tag, force=False, noprune=True)
                    logger.info(f"Janitor: removed leaked image tag {tag} ({size_mb:.0f} MB)")
                removed += 1
        except docker.errors.NotFound:
            continue
        except Exception as e:
            logger.warning(f"Janitor: failed to remove image {image_id[:19]}: {e}")

    return removed


def prune_caches_under_disk_pressure(*, disk_used_percent: float, pressure_percent: float, dry_run: bool) -> dict:
    """Aggressively prune build caches, but only when disk usage is at or above the threshold.

    Returns a summary dict so the caller can log the outcome.
    """
    summary = {"fired": False, "image_bytes": 0, "build_bytes": 0, "errors": 0}
    if disk_used_percent < pressure_percent:
        return summary

    summary["fired"] = True
    docker_client = get_long_timeout_docker_client()
    logger.info(f"Janitor: disk at {disk_used_percent:.0f}% (threshold {pressure_percent:.0f}%), pruning caches...")
    if dry_run:
        logger.info("Janitor (dry-run): would prune dangling images (until=1h) and the build cache")
        return summary

    try:
        result = docker_client.images.prune(filters={"dangling": True, "until": "1h"})
        summary["image_bytes"] = result.get("SpaceReclaimed", 0) or 0
        logger.info(f"Janitor: reclaimed {summary['image_bytes']} byte(s) from dangling images")
    except Exception as e:
        summary["errors"] += 1
        logger.warning(f"Janitor: failed to prune dangling images: {e}")

    try:
        result = docker_client.api.prune_builds()
        summary["build_bytes"] = result.get("SpaceReclaimed", 0) or 0
        logger.info(f"Janitor: reclaimed {summary['build_bytes']} byte(s) from the build cache")
    except Exception as e:
        summary["errors"] += 1
        logger.warning(f"Janitor: failed to prune build cache: {e}")

    return summary


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
