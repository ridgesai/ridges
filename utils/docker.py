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
HARBOR_IMAGE_REPO_PATTERN = re.compile(r".+__.+-(main|env-main|sandbox-proxy)$")
DOCKER_AUTOGEN_NAME_PATTERN = re.compile(r"^[a-z]+_[a-z]+[0-9]*$")
FULL_IMAGE_ID_PATTERN = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
SWEAP_IMAGE_PREFIX = "jefzda/sweap-images"
SWEBENCH_IMAGE_PREFIX = "swebench/sweb.eval."
RIDGES_TRIAL_ID_LABEL = "ridges.trial_id"


docker_client = None
docker_client_long_timeout = None


def _repo_from_image_ref(ref: str) -> str:
    """Return the repository portion of an ordinary 'repo:tag' reference.

    Digest references are deliberately rejected. The janitor operates on exact
    local tags and should never infer ownership from a digest or an unfamiliar
    registry-qualified reference.
    """
    if not ref or ref.startswith("sha256:") or "@" in ref:
        return ""

    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    repo = ref[:last_colon] if last_colon > last_slash else ref

    # Ridges-owned eval images are local Docker names
    first_component = repo.split("/", 1)[0]
    if ":" in first_component:
        return ""
    return repo


def image_ref_is_harbor_built(ref: str | None) -> bool:
    """Whether 'ref' is a locally-built Harbor trial image tag."""
    if not ref:
        return False
    repo = _repo_from_image_ref(ref)
    return bool(repo and "/" not in repo and HARBOR_IMAGE_REPO_PATTERN.fullmatch(repo))


def image_ref_is_pulled_eval(ref: str | None) -> bool:
    """Whether 'ref' is a re-pullable SWE-bench/SWEAP task image tag."""
    if not ref:
        return False
    repo = _repo_from_image_ref(ref)
    return bool(repo and (repo.startswith(SWEBENCH_IMAGE_PREFIX) or repo == SWEAP_IMAGE_PREFIX))


def image_ref_is_janitor_target(ref: str | None) -> bool:
    """Whether an image reference belongs to the validator evaluation lifecycle."""
    return image_ref_is_harbor_built(ref) or image_ref_is_pulled_eval(ref)


def _is_docker_autogen_name(name: str) -> bool:
    return bool(DOCKER_AUTOGEN_NAME_PATTERN.fullmatch(name))


def _container_restart_policy(container) -> str | None:
    """Return the configured restart policy, preserving missing metadata as unknown."""
    return ((container.attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")


def _container_image_refs(container) -> list[str]:
    refs = []
    config_image = (container.attrs.get("Config") or {}).get("Image")
    if config_image:
        refs.append(config_image)
    for key in ("Image", "ImageID"):
        value = container.attrs.get(key)
        if value:
            refs.append(value)
    return refs


def _container_runs_untagged_intermediate(container, client) -> bool:
    """Identify a classic-builder step created from an untagged full image ID."""
    config_image = (container.attrs.get("Config") or {}).get("Image") or ""
    if not FULL_IMAGE_ID_PATTERN.fullmatch(config_image):
        return False

    image_id = container.attrs.get("Image") or container.attrs.get("ImageID") or config_image
    try:
        image = client.images.get(image_id)
    except Exception:
        return False
    return not (image.attrs.get("RepoTags") or [])


def is_janitor_container(container, client) -> bool:
    """Classify only containers with strong Harbor/eval provenance.

    Harbor Compose names are explicit ownership. Docker-generated names require
    both a non-restarting policy and either an allowlisted eval image or the
    strict classic-builder intermediate signature.
    """
    if HARBOR_CONTAINER_NAME_PATTERN.fullmatch(container.name):
        return True
    if not _is_docker_autogen_name(container.name):
        return False
    if _container_restart_policy(container) not in ("", "no"):
        return False
    if any(image_ref_is_janitor_target(ref) for ref in _container_image_refs(container)):
        return True
    return _container_runs_untagged_intermediate(container, client)


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


def cleanup_harbor_docker_resources(
    *,
    dry_run: bool = False,
    stopped_grace_sec: float = 45 * 60,
    running_ttl_sec: float = 4 * 3600,
) -> dict:
    """Reconcile Docker leftovers when a validator process starts.

    Harbor-named and allowlisted eval containers belong to the previous process
    and cannot be resumed, so startup removes them immediately. Fresh classic-
    builder steps retain the same TTL protection as the hourly janitor because
    a PM2 restart alone does not prove that a daemon-side build step is stale.
    """
    client = get_docker_client()
    now = datetime.now(timezone.utc)
    removed = 0
    names = []
    errors = 0

    try:
        summaries = client.api.containers(all=True)
    except Exception as e:
        errors += 1
        logger.warning(f"Janitor startup: failed to list containers: {e}")
        summaries = []

    for summary in summaries:
        container_id = summary.get("Id") or ""
        name = container_id[:12]
        try:
            container = client.containers.get(container_id)
            name = container.name
            if not is_janitor_container(container, client):
                continue

            classic_step = _container_runs_untagged_intermediate(container, client)
            if classic_step and not _container_is_expired(
                container,
                now=now,
                stopped_grace_sec=stopped_grace_sec,
                running_ttl_sec=running_ttl_sec,
            ):
                continue

            container.reload()
            if not is_janitor_container(container, client):
                continue
            if classic_step and not _container_is_expired(
                container,
                now=now,
                stopped_grace_sec=stopped_grace_sec,
                running_ttl_sec=running_ttl_sec,
            ):
                continue

            if dry_run:
                logger.info(f"Janitor startup (dry-run): would remove container {container.name}")
            else:
                container.remove(force=True)
                logger.info(f"Janitor startup: removed container {container.name}")
            removed += 1
            if len(names) < 20:
                names.append(container.name)
        except docker.errors.NotFound:
            continue
        except Exception as e:
            errors += 1
            logger.warning(f"Janitor startup: failed to remove container {name}: {e}")

    try:
        networks = client.networks.list()
    except Exception as e:
        errors += 1
        logger.warning(f"Janitor startup: failed to list networks: {e}")
        networks = []

    for network in networks:
        try:
            if network.name in ("bridge", "host", "none"):
                continue

            if not HARBOR_NETWORK_NAME_PATTERN.fullmatch(network.name):
                continue

            network.reload()
            if network.attrs.get("Containers"):
                continue

            compose_network = (network.attrs.get("Labels") or {}).get("com.docker.compose.network")
            if compose_network not in ("sandbox_internal", "sandbox_egress"):
                continue

            if dry_run:
                logger.info(f"Janitor startup (dry-run): would remove network {network.name}")
            else:
                network.remove()
                logger.info(f"Janitor startup: removed network {network.name}")

        except docker.errors.NotFound:
            continue
        except Exception as e:
            errors += 1
            logger.warning(f"Janitor startup: failed to remove network {network.name}: {e}")

    return {"count": removed, "names": names, "errors": errors}


def prune_docker_disk_resources(
    *,
    include_build_cache: bool = False,
    dry_run: bool = False,
    until: str = "6h",
) -> dict:
    """Prune dangling image chains and optionally all unused, aged BuildKit cache.

    Untagging an image can expose another dangling parent only after a prune
    pass. Repeat until no bytes are reclaimed, capped at four passes so a broken
    or extremely deep daemon graph cannot monopolize the validator indefinitely.
    """
    summary = {"image_bytes": 0, "build_bytes": 0, "errors": 0}
    if dry_run:
        logger.info(
            f"Janitor (dry-run): would prune dangling images (until={until}, max_passes=4)"
            + (f" and all unused build cache older than {until}" if include_build_cache else "")
        )
        return summary

    docker_client = get_long_timeout_docker_client()
    logger.info("Pruning dangling Docker images...")
    for _ in range(4):
        try:
            result = docker_client.images.prune(filters={"dangling": True, "until": until})
            reclaimed = result.get("SpaceReclaimed", 0) or 0
            summary["image_bytes"] += reclaimed
            if reclaimed == 0:
                break
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Failed to prune dangling Docker images: {e}")
            break
    logger.info(f"Reclaimed {summary['image_bytes']} byte(s) from dangling Docker images")

    if include_build_cache:
        logger.info(f"Pruning all unused Docker build cache older than {until}...")
        try:
            result = docker_client.api.prune_builds(all=True, filters={"until": until})
            summary["build_bytes"] = result.get("SpaceReclaimed", 0) or 0
            logger.info(f"Reclaimed {summary['build_bytes']} byte(s) from Docker build cache")
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Failed to prune Docker build cache: {e}")

    return summary


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


def _container_is_expired(
    container,
    *,
    now: datetime,
    stopped_grace_sec: float,
    running_ttl_sec: float,
) -> bool:
    age = _age_seconds(_parse_docker_time(container.attrs.get("Created")), now)
    if age is None:
        return False

    if container.status in ("exited", "created", "dead"):
        return age > stopped_grace_sec

    if container.status == "running":
        return age > running_ttl_sec
    return False


def sweep_stale_harbor_containers(
    *,
    stopped_grace_sec: float,
    running_ttl_sec: float,
    dry_run: bool,
) -> dict:
    """
    Remove leaked Harbor trial containers (and then detached Harbor networks).
    Returns the count, a bounded list of container names, and error count.
    """
    docker_client = get_docker_client()
    now = datetime.now(timezone.utc)
    removed = 0
    removed_names = []
    errors = 0

    try:
        summaries = docker_client.api.containers(all=True)
    except Exception as e:
        logger.warning(f"Janitor: failed to list containers: {e}")
        return {"count": 0, "names": [], "errors": 1}

    for summary in summaries:
        names = [n.lstrip("/") for n in (summary.get("Names") or [])]
        name = names[0] if names else (summary.get("Id") or "")[:12]
        try:
            container = docker_client.containers.get(summary.get("Id") or name)
            name = container.name
            if not is_janitor_container(container, docker_client):
                continue

            if not _container_is_expired(
                container,
                now=now,
                stopped_grace_sec=stopped_grace_sec,
                running_ttl_sec=running_ttl_sec,
            ):
                continue

            container.reload()
            if not is_janitor_container(container, docker_client):
                continue

            if not _container_is_expired(
                container,
                now=now,
                stopped_grace_sec=stopped_grace_sec,
                running_ttl_sec=running_ttl_sec,
            ):
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
            if len(removed_names) < 20:
                removed_names.append(container.name)
        except docker.errors.NotFound:
            continue
        except Exception as e:
            errors += 1
            logger.warning(f"Janitor: failed to remove container {name}: {e}")

    try:
        networks = docker_client.networks.list()
    except Exception as e:
        errors += 1
        logger.warning(f"Janitor: failed to list networks: {e}")
        return {"count": removed, "names": removed_names, "errors": errors}

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
            errors += 1
            logger.warning(f"Janitor: failed to remove network {network.name}: {e}")

    return {"count": removed, "names": removed_names, "errors": errors}


def _referenced_image_values(client) -> set[str]:
    values = set()
    for summary in client.api.containers(all=True):
        for key in ("ImageID", "Image"):
            value = summary.get(key)
            if value:
                values.add(value)
    return values


def sweep_leaked_harbor_images(
    *,
    tag_grace_sec: float,
    dry_run: bool,
    disk_used_percent: float | None,
    pulled_image_pressure_percent: float = 50.0,
) -> dict:
    """Untag unreferenced evaluation images using population-specific rules.

    Locally-built Harbor tags require a usable LastTagTime older than the grace
    period. Pulled SWE-bench/SWEAP tags are re-pullable cache and become eligible
    only under disk pressure. Every deletion is non-forced.
    """
    docker_client = get_docker_client()
    now = datetime.now(timezone.utc)
    removed = 0
    names = []
    errors = 0

    try:
        referenced_image_values = _referenced_image_values(docker_client)
        image_summaries = docker_client.api.images()
    except Exception as e:
        logger.warning(f"Janitor: failed to list images/containers: {e}")
        return {"count": 0, "names": [], "errors": 1}

    for summary in image_summaries:
        image_id = summary.get("Id") or ""
        try:
            repo_tags = summary.get("RepoTags") or []
            harbor_tags = [tag for tag in repo_tags if image_ref_is_harbor_built(tag)]
            pulled_tags = [tag for tag in repo_tags if image_ref_is_pulled_eval(tag)]
            if not harbor_tags and not pulled_tags:
                continue
            if image_id in referenced_image_values or any(tag in referenced_image_values for tag in repo_tags):
                continue

            image = docker_client.images.get(image_id)
            last_tag_time = _parse_docker_time((image.attrs.get("Metadata") or {}).get("LastTagTime"))
            age = _age_seconds(last_tag_time, now)
            eligible_tags = []
            if age is not None and age > tag_grace_sec:
                eligible_tags.extend(harbor_tags)
            if disk_used_percent is not None and disk_used_percent >= pulled_image_pressure_percent:
                eligible_tags.extend(pulled_tags)

            size_mb = (image.attrs.get("Size") or 0) / 1e6
            for tag in eligible_tags:
                try:
                    current_refs = _referenced_image_values(docker_client)
                    if image_id in current_refs or tag in current_refs:
                        continue

                    current_image = docker_client.images.get(tag)
                    if current_image.id != image_id:
                        continue

                    if dry_run:
                        logger.info(f"Janitor (dry-run): would untag leaked image {tag} ({size_mb:.0f} MB)")
                    else:
                        docker_client.images.remove(tag, force=False, noprune=True)
                        logger.info(f"Janitor: removed leaked image tag {tag} ({size_mb:.0f} MB)")
                    removed += 1
                    if len(names) < 20:
                        names.append(tag)
                except docker.errors.NotFound:
                    continue
                except Exception as e:
                    errors += 1
                    logger.warning(f"Janitor: failed to remove image tag {tag}: {e}")
        except docker.errors.NotFound:
            continue
        except Exception as e:
            errors += 1
            logger.warning(f"Janitor: failed to remove image {image_id[:19]}: {e}")

    return {"count": removed, "names": names, "errors": errors}


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
        logger.info("Janitor (dry-run): would prune dangling images and all unused build cache older than 1h")
        return summary

    try:
        result = docker_client.images.prune(filters={"dangling": True, "until": "1h"})
        summary["image_bytes"] = result.get("SpaceReclaimed", 0) or 0
        logger.info(f"Janitor: reclaimed {summary['image_bytes']} byte(s) from dangling images")
    except Exception as e:
        summary["errors"] += 1
        logger.warning(f"Janitor: failed to prune dangling images: {e}")

    try:
        result = docker_client.api.prune_builds(all=True, filters={"until": "1h"})
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
