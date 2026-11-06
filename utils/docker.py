import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

import docker
import psutil

from utils.task_cache import cached_task_last_used

logger = logging.getLogger(__name__)

DOCKER_PREFIX = "ridges-ai"
HARBOR_CONTAINER_NAME_PATTERN = re.compile(r".+__.+-(main|sandbox-proxy)-1$")
HARBOR_NETWORK_NAME_PATTERN = re.compile(r".+__.+_sandbox_(internal|egress)$")
HARBOR_IMAGE_REPO_PATTERN = re.compile(r".+__.+-(main|env-main|sandbox-proxy)$")
HARBOR_COMPOSE_PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*__[a-z0-9]{7}(?:__env|__verifier__[a-z0-9_-]+)")
HARBOR_COMPOSE_IMAGE_REPO_PATTERN = re.compile(rf"{HARBOR_COMPOSE_PROJECT_PATTERN.pattern}-[a-z0-9][a-z0-9_.-]*")
HARBOR_CONTENT_IMAGE_REPO_PATTERN = re.compile(r"hb__[0-9a-f]{32}")
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_NETWORK_LABEL = "com.docker.compose.network"
DOCKER_AUTOGEN_NAME_PATTERN = re.compile(r"^[a-z]+_[a-z]+[0-9]*$")
FULL_IMAGE_ID_PATTERN = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
RIDGES_TRIAL_ID_LABEL = "ridges.trial_id"
PRESSURE_PRUNE_HEADROOM_PERCENT = 10
PRESSURE_EVICTION_MIN_IDLE_SECONDS = 3600
TASK_DIGEST_LABEL = "org.ridges.task.digest"
KEEP_IMAGE_TAG_PREFIX = "harbor-prebuilt:"


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
    return bool(
        repo
        and "/" not in repo
        and (
            HARBOR_IMAGE_REPO_PATTERN.fullmatch(repo)
            or HARBOR_COMPOSE_IMAGE_REPO_PATTERN.fullmatch(repo)
            or HARBOR_CONTENT_IMAGE_REPO_PATTERN.fullmatch(repo)
        )
    )


def image_ref_is_janitor_target(ref: str | None) -> bool:
    """Whether an image reference belongs to the validator evaluation lifecycle."""
    return image_ref_is_harbor_built(ref)


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

    Current Compose services require a Harbor project signature, matching
    project/service labels and the generated container name. This covers DB
    sidecars without relying on their image or a fixed list of service names.
    Legacy main/proxy names remain supported. Docker-generated names require a
    non-restarting policy plus eval-image or classic-builder provenance.
    """
    labels = (container.attrs.get("Config") or {}).get("Labels") or {}
    project = labels.get(COMPOSE_PROJECT_LABEL) or ""
    service = labels.get(COMPOSE_SERVICE_LABEL) or ""
    if project or service:
        if (
            (
                HARBOR_COMPOSE_PROJECT_PATTERN.fullmatch(project)
                or HARBOR_CONTAINER_NAME_PATTERN.fullmatch(container.name)
            )
            and service
            and re.fullmatch(rf"{re.escape(project)}-{re.escape(service)}-[1-9][0-9]*", container.name)
        ):
            return True
    elif HARBOR_CONTAINER_NAME_PATTERN.fullmatch(container.name):
        return True
    if not _is_docker_autogen_name(container.name):
        return False
    if _container_restart_policy(container) not in ("", "no"):
        return False
    if any(image_ref_is_janitor_target(ref) for ref in _container_image_refs(container)):
        return True
    return _container_runs_untagged_intermediate(container, client)


def _is_janitor_network(network) -> bool:
    labels = network.attrs.get("Labels") or {}
    project = labels.get(COMPOSE_PROJECT_LABEL) or ""
    compose_network = labels.get(COMPOSE_NETWORK_LABEL) or ""
    if project:
        return bool(
            (
                HARBOR_COMPOSE_PROJECT_PATTERN.fullmatch(project)
                or (
                    HARBOR_NETWORK_NAME_PATTERN.fullmatch(network.name)
                    and compose_network in ("sandbox_internal", "sandbox_egress")
                )
            )
            and compose_network
            and network.name == f"{project}_{compose_network}"
        )
    return bool(
        HARBOR_NETWORK_NAME_PATTERN.fullmatch(network.name)
        and compose_network in ("sandbox_internal", "sandbox_egress")
    )


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
                container.remove(force=True, v=True)
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

            if not _is_janitor_network(network):
                continue

            network.reload()
            if not _is_janitor_network(network) or network.attrs.get("Containers"):
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


def _remove_dangling_build_leftovers(docker_client, *, until: str) -> int:
    """Remove dangling images that carry no registry digest and return their summed size.

    Docker also reports an image pulled only by digest (`image@sha256:...`) as
    dangling because it has no tag. Those are task sidecars and base images that
    a starting trial may be about to use, so only the start-gated pressure prune
    may evict them. An image still used by a container is refused by the daemon
    and skipped.
    """
    reclaimed = 0
    for summary in docker_client.api.images(filters={"dangling": True, "until": until}):
        if any("<none>" not in digest for digest in summary.get("RepoDigests") or []):
            continue
        try:
            docker_client.api.remove_image(summary["Id"], force=False, noprune=False)
            reclaimed += summary.get("Size") or 0
        except docker.errors.NotFound:
            continue
        except docker.errors.APIError as e:
            if e.status_code != 409:
                raise
    return reclaimed


def prune_docker_disk_resources(*, dry_run: bool = False, until: str = "6h") -> dict:
    """Remove dangling build leftovers, keeping digest-pinned registry images.

    Pulled images are only evicted by the start-gated
    `prune_caches_under_disk_pressure`; build cache by that and
    `prune_build_cache_older_than`.

    Untagging an image can expose another dangling parent only after a prune
    pass. Repeat until no bytes are reclaimed, capped at four passes so a broken
    or extremely deep daemon graph cannot monopolize the validator indefinitely.
    """
    summary = {"image_bytes": 0, "errors": 0}
    if dry_run:
        logger.info(f"Janitor (dry-run): would prune dangling build leftovers (until={until}, max_passes=4)")
        return summary

    docker_client = get_long_timeout_docker_client()
    logger.info("Pruning dangling Docker images...")
    for _ in range(4):
        try:
            reclaimed = _remove_dangling_build_leftovers(docker_client, until=until)
            summary["image_bytes"] += reclaimed
            if reclaimed == 0:
                break
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Failed to prune dangling Docker images: {e}")
            break
    logger.info(f"Reclaimed {summary['image_bytes']} byte(s) from dangling Docker images")
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
                container.remove(force=container.status == "running", v=True)
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
            if not _is_janitor_network(network):
                continue
            age = _age_seconds(_parse_docker_time(network.attrs.get("Created")), now)
            if age is None or age <= stopped_grace_sec:
                continue
            network.reload()
            if not _is_janitor_network(network) or network.attrs.get("Containers"):
                continue
            age = _age_seconds(_parse_docker_time(network.attrs.get("Created")), now)
            if age is None or age <= stopped_grace_sec:
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
) -> dict:
    """Untag unreferenced locally-built Harbor images whose LastTagTime is older than the grace period.

    `hb__` images are shared by every trial of the same environment, but each
    native start rebuilds and re-tags its image, so an image idle past the grace
    period is not part of a start. Pulled images are reclaimed only by the
    start-gated `prune_caches_under_disk_pressure`. Every deletion is non-forced.
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
            if not harbor_tags:
                continue
            if image_id in referenced_image_values or any(tag in referenced_image_values for tag in repo_tags):
                continue

            image = docker_client.images.get(image_id)
            last_tag_time = _parse_docker_time((image.attrs.get("Metadata") or {}).get("LastTagTime"))
            age = _age_seconds(last_tag_time, now)
            if age is None or age <= tag_grace_sec:
                continue

            size_mb = (image.attrs.get("Size") or 0) / 1e6
            for tag in harbor_tags:
                try:
                    current_refs = _referenced_image_values(docker_client)
                    if image_id in current_refs or tag in current_refs:
                        continue

                    current_image = docker_client.images.get(tag)
                    if current_image.id != image_id:
                        continue
                    current_age = _age_seconds(
                        _parse_docker_time((current_image.attrs.get("Metadata") or {}).get("LastTagTime")), now
                    )
                    if current_age is None or current_age <= tag_grace_sec:
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


def _disk_total_bytes() -> int:
    """Size of the filesystem `get_system_metrics` reports `disk_percent` for."""
    return shutil.disk_usage("/").total


def _disk_used_percent() -> float:
    """Measured the same way as `get_system_metrics().disk_percent`."""
    return psutil.disk_usage("/").percent


def _build_cache_bytes(docker_client) -> int:
    """Total BuildKit cache size, queried alone because a full `docker system df`
    also sizes live containers and fails on busy containerd-store hosts."""
    api = docker_client.api
    usage = api._result(api._get(api._url("/system/df"), params={"type": "build-cache"}), json=True)
    return sum(record.get("Size") or 0 for record in usage.get("BuildCache") or [])


def prune_build_cache_older_than(*, hours: int, dry_run: bool) -> dict:
    """Remove build cache records unused for `hours`, regardless of disk pressure.

    BuildKit's `until` filter measures time since a record was last used, and it
    never removes records held by a running build, so this needs no start gate:
    a removed cold record only costs a rebuilt step.
    """
    summary = {"build_bytes": 0, "errors": 0}
    if dry_run:
        logger.info(f"Janitor (dry-run): would remove build cache unused for {hours}h")
        return summary
    try:
        result = get_long_timeout_docker_client().api.prune_builds(all=True, filters={"until": f"{hours}h"})
        summary["build_bytes"] = result.get("SpaceReclaimed", 0) or 0
        logger.info(f"Janitor: removed {summary['build_bytes']} byte(s) of build cache unused for {hours}h")
    except Exception as e:
        summary["errors"] += 1
        logger.warning(f"Janitor: failed to prune old build cache: {e}")
    return summary


def _image_refs(summary: dict) -> list[str]:
    refs = list(summary.get("RepoTags") or []) + list(summary.get("RepoDigests") or [])
    return [ref for ref in refs if "<none>" not in ref]


def _image_last_used(docker_client, summary: dict) -> float:
    """Epoch seconds an image was last used; 0 when unknown, so it ranks oldest.

    Task images share their task's cache directory mtime, which every lookup and
    trial end refreshes. Other images only have their last tag time.
    """
    task_digest = (summary.get("Labels") or {}).get(TASK_DIGEST_LABEL)
    if task_digest:
        return cached_task_last_used(task_digest) or 0.0
    try:
        metadata = docker_client.api.inspect_image(summary["Id"]).get("Metadata") or {}
    except docker.errors.NotFound:
        return 0.0
    last_tag_time = _parse_docker_time(metadata.get("LastTagTime"))
    return last_tag_time.timestamp() if last_tag_time else 0.0


def _evict_images_least_recently_used(
    docker_client, *, target_percent: float, active_task_digests: frozenset[str], summary: dict, dry_run: bool
) -> None:
    """Delete unreferenced images, least recently used first, until disk is at the target.

    Skips images a container references (running or stopped), images of tasks the
    validator is running, Harbor's egress sidecar, and anything used within
    PRESSURE_EVICTION_MIN_IDLE_SECONDS. An image with no known use (a digest-only
    sidecar on the classic store has no LastTagTime) ranks oldest and is not
    protected by that floor. Disk is re-measured after every image because shared
    layers make image sizes unreliable as reclaimed bytes.
    """
    if not dry_run and _disk_used_percent() <= target_percent:
        return
    referenced = _referenced_image_values(docker_client)
    candidates = []
    for image in docker_client.api.images():
        refs = _image_refs(image)
        if image["Id"] in referenced or any(ref in referenced for ref in refs):
            continue

        if any(ref.startswith(KEEP_IMAGE_TAG_PREFIX) for ref in refs):
            continue

        if (image.get("Labels") or {}).get(TASK_DIGEST_LABEL) in active_task_digests:
            continue
        try:
            candidates.append((_image_last_used(docker_client, image), image["Id"], image))
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Janitor: skipping image {image['Id'][:19]}, could not read its last use: {e}")
    candidates.sort(key=lambda candidate: candidate[:2])

    dry_run_logged = 0
    for last_used, image_id, image in candidates:
        if not dry_run and _disk_used_percent() <= target_percent:
            return

        if time.time() - last_used < PRESSURE_EVICTION_MIN_IDLE_SECONDS:
            continue
        refs = _image_refs(image)
        name = refs[0] if refs else image_id[:19]
        if dry_run:
            if dry_run_logged == 20:
                return
            logger.info(f"Janitor (dry-run): would evict image {name}, if disk stays above {target_percent:.0f}%")
            dry_run_logged += 1
            continue

        try:
            current_refs = _referenced_image_values(docker_client)
            if image_id in current_refs or any(ref in current_refs for ref in refs):
                continue

            if time.time() - _image_last_used(docker_client, image) < PRESSURE_EVICTION_MIN_IDLE_SECONDS:
                continue

            for ref in [*refs, image_id]:
                try:
                    docker_client.api.remove_image(ref, force=False, noprune=False)
                except docker.errors.NotFound:
                    continue
            summary["evicted_images"] += 1
            logger.info(f"Janitor: evicted image {name}")
        except docker.errors.APIError as e:
            if e.status_code != 409:
                summary["errors"] += 1
                logger.warning(f"Janitor: failed to evict image {name}: {e}")


def prune_caches_under_disk_pressure(
    *,
    disk_used_percent: float,
    pressure_percent: float,
    dry_run: bool,
    active_task_digests: frozenset[str] = frozenset(),
) -> dict:
    """Free disk down to PRESSURE_PRUNE_HEADROOM_PERCENT below the threshold, least recently used first.

    Untagged build leftovers go first, then the build cache is trimmed to a size
    budget, which BuildKit enforces by evicting its least recently used records.
    If disk is still above the target, unreferenced images are deleted least
    recently used first, including digest-pinned sidecars.

    This evicts images and cache that a starting trial may need, so the caller
    must hold the Docker start gate exclusively while it runs.

    Returns a summary dict so the caller can log the outcome.
    """
    summary = {"fired": False, "image_bytes": 0, "build_bytes": 0, "evicted_images": 0, "errors": 0}
    if disk_used_percent < pressure_percent:
        return summary

    summary["fired"] = True
    target_percent = pressure_percent - PRESSURE_PRUNE_HEADROOM_PERCENT
    bytes_to_free = (disk_used_percent - target_percent) / 100 * _disk_total_bytes()
    docker_client = get_long_timeout_docker_client()
    logger.info(
        f"Janitor: disk at {disk_used_percent:.0f}% (threshold {pressure_percent:.0f}%), "
        f"freeing ~{bytes_to_free / 1e9:.0f} GB toward {target_percent:.0f}%..."
    )
    if dry_run:
        logger.info(
            "Janitor (dry-run): would prune build leftovers and trim the build cache, least recently used first"
        )
    else:
        try:
            summary["image_bytes"] = _remove_dangling_build_leftovers(docker_client, until="1h")
            logger.info(f"Janitor: removed {summary['image_bytes']} byte(s) of dangling build leftovers")
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Janitor: failed to prune dangling build leftovers: {e}")

        try:
            bytes_to_free = max(_disk_used_percent() - target_percent, 0) / 100 * _disk_total_bytes()
            cache_bytes = _build_cache_bytes(docker_client)
            keep_bytes = max(int(cache_bytes - bytes_to_free), 0)
            if keep_bytes < cache_bytes:
                result = docker_client.api.prune_builds(all=True, keep_storage=keep_bytes)
                summary["build_bytes"] = result.get("SpaceReclaimed", 0) or 0
            logger.info(
                f"Janitor: trimmed the build cache from {cache_bytes / 1e9:.0f} GB to a "
                f"{keep_bytes / 1e9:.0f} GB budget, reclaiming {summary['build_bytes']} byte(s)"
            )
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Janitor: failed to trim build cache: {e}")

    try:
        _evict_images_least_recently_used(
            docker_client,
            target_percent=target_percent,
            active_task_digests=active_task_digests,
            summary=summary,
            dry_run=dry_run,
        )
    except Exception as e:
        summary["errors"] += 1
        logger.warning(f"Janitor: failed to evict images: {e}")

    if not dry_run:
        logger.info(f"Janitor: disk at {_disk_used_percent():.0f}% after the pressure prune")
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
