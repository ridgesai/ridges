from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import utils.system_metrics as metrics_module
import validator.config as config
from utils.system_metrics import SystemMetrics, collect_system_metrics

DOCKER_ROOT = "/mnt/docker-volume/docker"


@pytest.fixture
def storage_host(monkeypatch):
    metrics_module._discover_docker_storage_path.cache_clear()
    monkeypatch.setattr(config, "RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setattr(config, "DOCKER_STORAGE_METRICS_PATH", None)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(metrics_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(metrics_module.Path, "exists", lambda _path: False)
    monkeypatch.setattr(metrics_module, "get_num_docker_containers", lambda: 0)
    monkeypatch.setattr(metrics_module, "_read_cpu_model", lambda: "test CPU")
    monkeypatch.setattr(metrics_module.psutil, "cpu_percent", lambda: 0.5)
    monkeypatch.setattr(metrics_module.psutil, "virtual_memory", lambda: SimpleNamespace(percent=9.5, total=64e9))

    root_usage = SimpleNamespace(percent=24.7, total=1000e9, free=753e9)
    docker_usage = SimpleNamespace(percent=99.0, total=200e9, free=2e9)
    usage = Mock(side_effect=lambda path: root_usage if path == "/" else docker_usage)
    monkeypatch.setattr(metrics_module.psutil, "disk_usage", usage)
    inodes = Mock(return_value=SimpleNamespace(f_files=1000, f_ffree=0))
    monkeypatch.setattr(metrics_module.os, "statvfs", inodes)

    client = Mock()
    client.api.base_url = "http+docker://localhost"
    client.info.return_value = {"DockerRootDir": DOCKER_ROOT}
    factory = Mock(return_value=client)
    monkeypatch.setattr(metrics_module.docker, "from_env", factory)
    yield SimpleNamespace(client=client, factory=factory, usage=usage, inodes=inodes)
    metrics_module._discover_docker_storage_path.cache_clear()


def test_separate_docker_volume_does_not_replace_root_metric(storage_host):
    metrics = collect_system_metrics()

    assert metrics.disk_percent == 24.7
    assert metrics.disk_total_gb == 1000
    assert metrics.docker_disk_percent == 99
    assert metrics.docker_disk_total_gb == 200
    assert metrics.docker_disk_free_gb == 2
    assert metrics.docker_disk_inodes_percent == 100
    assert metrics.docker_disk_path == DOCKER_ROOT
    assert metrics.num_containers == 0
    assert [call.args[0] for call in storage_host.usage.call_args_list] == ["/", DOCKER_ROOT]
    storage_host.factory.assert_called_once_with(timeout=2)
    storage_host.client.close.assert_called_once()


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_inaccessible_docker_volume_never_falls_back_to_root(storage_host, error):
    original_usage = storage_host.usage.side_effect

    def usage(path):
        if path != "/":
            raise error(path)
        return original_usage(path)

    storage_host.usage.side_effect = usage
    metrics = collect_system_metrics()
    assert metrics.disk_percent == 24.7
    assert metrics.cpu_percent == 0.5
    assert all(value is None for key, value in metrics.model_dump().items() if key.startswith("docker_disk_"))


@pytest.mark.parametrize("docker_root", [None, "", "relative/docker"])
def test_missing_or_invalid_daemon_path_is_unavailable(storage_host, docker_root):
    storage_host.client.info.return_value = {"DockerRootDir": docker_root}
    assert collect_system_metrics().docker_disk_percent is None
    storage_host.usage.assert_called_once_with("/")


@pytest.mark.parametrize("platform_name", ["Darwin", "Windows"])
def test_other_os_cannot_mistake_daemon_vm_path_for_local_storage(storage_host, monkeypatch, platform_name):
    monkeypatch.setattr(metrics_module.platform, "system", lambda: platform_name)
    assert collect_system_metrics().docker_disk_percent is None
    storage_host.factory.assert_not_called()


@pytest.mark.parametrize("endpoint", ["tcp://docker-host:2376", "ssh://docker-host"])
def test_remote_daemon_cannot_measure_an_unrelated_local_directory(storage_host, monkeypatch, endpoint):
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    assert collect_system_metrics().docker_disk_percent is None
    storage_host.factory.assert_not_called()


@pytest.mark.parametrize("marker", ["/.dockerenv", "/run/.containerenv"])
def test_container_requires_explicit_backing_filesystem_mount(storage_host, monkeypatch, marker):
    monkeypatch.setattr(metrics_module.Path, "exists", lambda path: str(path) == marker)
    assert collect_system_metrics().docker_disk_percent is None
    storage_host.factory.assert_not_called()

    monkeypatch.setattr(config, "DOCKER_STORAGE_METRICS_PATH", "/host/docker-storage")
    metrics = collect_system_metrics()
    assert metrics.docker_disk_path == "/host/docker-storage"
    assert metrics.docker_disk_percent == 99
    storage_host.factory.assert_not_called()


def test_relative_override_is_unavailable(storage_host, monkeypatch):
    monkeypatch.setattr(config, "DOCKER_STORAGE_METRICS_PATH", "relative/path")
    assert collect_system_metrics().docker_disk_percent is None
    storage_host.factory.assert_not_called()


@pytest.mark.parametrize("inode_count", [0, None])
def test_missing_inode_measurement_preserves_byte_metrics(storage_host, inode_count):
    if inode_count is None:
        storage_host.inodes.side_effect = OSError("inode stats unavailable")
    else:
        storage_host.inodes.return_value = SimpleNamespace(f_files=inode_count, f_ffree=0)
    metrics = collect_system_metrics()
    assert metrics.docker_disk_percent == 99
    assert metrics.docker_disk_inodes_percent is None


def test_discovery_is_cached_but_usage_is_refreshed(storage_host, monkeypatch):
    monkeypatch.setattr(metrics_module.time, "monotonic", lambda: 120)
    assert collect_system_metrics().docker_disk_percent == 99
    storage_host.usage.return_value = SimpleNamespace(percent=50, total=200e9, free=100e9)
    storage_host.usage.side_effect = None
    assert collect_system_metrics().docker_disk_percent == 50
    storage_host.client.info.assert_called_once()

    storage_host.client.info.return_value = {"DockerRootDir": "/new/docker-root"}
    monkeypatch.setattr(metrics_module.time, "monotonic", lambda: 180)
    assert collect_system_metrics().docker_disk_path == "/new/docker-root"
    assert storage_host.client.info.call_count == 2


def test_discovery_failure_keeps_other_metrics_and_retries_next_minute(storage_host, monkeypatch):
    monkeypatch.setattr(metrics_module.time, "monotonic", lambda: 120)
    storage_host.client.info.side_effect = TimeoutError("daemon busy")
    assert collect_system_metrics().docker_disk_percent is None
    assert collect_system_metrics().ram_percent == 9.5
    storage_host.client.info.assert_called_once()
    storage_host.client.close.assert_called_once()

    monkeypatch.setattr(metrics_module.time, "monotonic", lambda: 180)
    storage_host.client.info.side_effect = None
    assert collect_system_metrics().docker_disk_percent == 99


def test_container_listing_failure_does_not_hide_docker_disk(storage_host, monkeypatch):
    monkeypatch.setattr(metrics_module, "get_num_docker_containers", Mock(side_effect=TimeoutError("docker ps")))
    metrics = collect_system_metrics()
    assert metrics.disk_percent == 24.7
    assert metrics.docker_disk_percent == 99
    assert metrics.num_containers is None


def test_kubernetes_does_not_probe_local_docker_storage(storage_host, monkeypatch):
    import utils.k8s

    monkeypatch.setattr(config, "RIDGES_ENVIRONMENT_TYPE", "kubernetes")
    monkeypatch.setattr(utils.k8s, "get_num_k8s_eval_pods", lambda: 3)
    metrics = collect_system_metrics()
    assert metrics.num_containers == 3
    assert metrics.disk_percent == 24.7
    assert metrics.docker_disk_percent is None
    storage_host.factory.assert_not_called()


def test_old_heartbeat_metrics_remain_valid():
    metrics = SystemMetrics.model_validate({"disk_percent": 24.7, "num_containers": 0})
    assert metrics.disk_percent == 24.7
    assert metrics.docker_disk_percent is None
