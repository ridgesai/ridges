import logging
import os
import platform
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import docker
import psutil
from pydantic import BaseModel

from utils.docker import get_num_docker_containers

logger = logging.getLogger(__name__)


class SystemMetrics(BaseModel):
    """
    cpu_percent: CPU percentage (0-100)
    cpu_model: CPU model name when exposed by Linux
    cpu_physical_cores: Physical CPU core count visible to the validator
    cpu_logical_cpus: Logical CPU count (not adjusted for container CPU quotas)
    cpu_architecture: Machine architecture
    ram_percent: RAM percentage (0-100)
    ram_total_gb: Total RAM in GB
    disk_percent: Disk percentage (0-100)
    disk_total_gb: Total disk in GB
    docker_disk_percent: Used space on Docker's backing filesystem (0-100)
    docker_disk_total_gb: Docker filesystem capacity in GB, not image sizes
    docker_disk_free_gb: Docker filesystem space available in GB
    docker_disk_inodes_percent: Used inodes on Docker's backing filesystem (0-100)
    docker_disk_path: Local directory used to measure Docker storage
    num_containers: Number of running containers (Docker or k8s eval Pods)
    """

    cpu_percent: Optional[float] = None
    cpu_model: Optional[str] = None
    cpu_physical_cores: Optional[int] = None
    cpu_logical_cpus: Optional[int] = None
    cpu_architecture: Optional[str] = None
    ram_percent: Optional[float] = None
    ram_total_gb: Optional[float] = None
    disk_percent: Optional[float] = None
    disk_total_gb: Optional[float] = None
    docker_disk_percent: Optional[float] = None
    docker_disk_total_gb: Optional[float] = None
    docker_disk_free_gb: Optional[float] = None
    docker_disk_inodes_percent: Optional[float] = None
    docker_disk_path: Optional[str] = None
    num_containers: Optional[int] = None


@lru_cache(maxsize=1)
def _discover_docker_storage_path(_cache_period: int) -> str | None:
    """Cache discovery (including failures) for one minute, not usage readings.

    DockerRootDir belongs to the daemon's mount namespace. Only auto-detect it
    for a local Linux host; other deployments need an explicit mounted path.
    """
    if (
        platform.system() != "Linux"
        or Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
        or not os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock").startswith("unix://")
    ):
        return None

    try:
        client = docker.from_env(timeout=2)
        try:
            if client.api.base_url != "http+docker://localhost":
                return None
            path = client.info().get("DockerRootDir")
            if isinstance(path, str) and Path(path).is_absolute():
                return path
        finally:
            client.close()
    except Exception:
        logger.debug("Could not discover Docker storage path", exc_info=True)
    return None


def _collect_docker_storage_metrics(metrics: SystemMetrics, path_override: str | None) -> None:
    path = path_override or _discover_docker_storage_path(int(time.monotonic() // 60))
    if not path or not Path(path).is_absolute():
        return
    try:
        disk = psutil.disk_usage(path)
        metrics.docker_disk_percent = disk.percent
        metrics.docker_disk_total_gb = disk.total / (1000**3)
        metrics.docker_disk_free_gb = disk.free / (1000**3)
        metrics.docker_disk_path = path
    except OSError:
        # Never substitute root-disk usage for an inaccessible Docker volume.
        logger.debug("Could not measure Docker storage at %s", path, exc_info=True)
        return

    try:
        filesystem = os.statvfs(path)
        if filesystem.f_files > 0:
            metrics.docker_disk_inodes_percent = 100 * (1 - filesystem.f_ffree / filesystem.f_files)
    except OSError:
        logger.debug("Could not measure Docker storage inodes at %s", path, exc_info=True)


def _read_cpu_model() -> str | None:
    with Path("/proc/cpuinfo").open(encoding="utf-8", errors="replace") as cpuinfo:
        for line in cpuinfo.read(16_384).splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "model name":
                return value.strip() or None
    return None


def collect_system_metrics() -> SystemMetrics:
    metrics = SystemMetrics()

    try:
        metrics.cpu_percent = psutil.cpu_percent()

        memory = psutil.virtual_memory()
        metrics.ram_percent = memory.percent
        metrics.ram_total_gb = memory.total / (1000**3)

        disk = psutil.disk_usage("/")
        metrics.disk_percent = disk.percent
        metrics.disk_total_gb = disk.total / (1000**3)

        from validator.config import RIDGES_ENVIRONMENT_TYPE

        if RIDGES_ENVIRONMENT_TYPE == "kubernetes":
            from utils.k8s import get_num_k8s_eval_pods

            metrics.num_containers = get_num_k8s_eval_pods()
        else:
            metrics.num_containers = get_num_docker_containers()

    except Exception as e:
        logger.warning(f"Error in get_system_metrics(): {e}")

    try:
        from validator.config import DOCKER_STORAGE_METRICS_PATH, RIDGES_ENVIRONMENT_TYPE

        if RIDGES_ENVIRONMENT_TYPE == "docker":
            _collect_docker_storage_metrics(metrics, DOCKER_STORAGE_METRICS_PATH)
    except Exception:
        logger.debug("Could not collect Docker storage metrics", exc_info=True)

    for field, probe in (
        ("cpu_model", _read_cpu_model),
        ("cpu_physical_cores", lambda: psutil.cpu_count(logical=False)),
        ("cpu_logical_cpus", lambda: psutil.cpu_count(logical=True)),
        ("cpu_architecture", platform.machine),
    ):
        try:
            setattr(metrics, field, probe() or None)
        except Exception:
            logger.debug("Could not collect optional metric %s", field, exc_info=True)

    return metrics


async def get_system_metrics() -> SystemMetrics:
    return collect_system_metrics()
