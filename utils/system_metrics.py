import logging
import platform
from pathlib import Path
from typing import Optional

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
    num_containers: Optional[int] = None


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
