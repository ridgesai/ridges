from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from ridges_harbor.k8s_environment import build_isolated_k8s_apis


def test_isolated_apis_use_fresh_configuration_and_api_client(monkeypatch) -> None:
    configurations: list[k8s_client.Configuration] = []

    def fake_incluster(client_configuration=None) -> None:
        configurations.append(client_configuration)
        client_configuration.host = "https://kubernetes.example.test"

    monkeypatch.setattr(
        "ridges_harbor.k8s_environment.k8s_config.load_incluster_config",
        fake_incluster,
    )

    core1, batch1 = build_isolated_k8s_apis()
    core2, batch2 = build_isolated_k8s_apis()
    try:
        assert core1 is not core2
        assert core1.api_client is not core2.api_client
        assert core1.api_client is batch1.api_client
        assert core2.api_client is batch2.api_client
        assert configurations[0] is not configurations[1]
        assert core1.api_client.configuration is configurations[0]
        assert core2.api_client.configuration is configurations[1]
    finally:
        core1.api_client.close()
        core2.api_client.close()


def test_isolated_apis_fall_back_to_kubeconfig(monkeypatch) -> None:
    def fake_incluster(client_configuration=None) -> None:
        raise k8s_config.ConfigException("no service account")

    kube_configs: list[k8s_client.Configuration] = []

    def fake_kubeconfig(*, context=None, client_configuration=None) -> None:
        kube_configs.append(client_configuration)
        client_configuration.host = "https://kubeconfig.example.test"

    monkeypatch.setattr(
        "ridges_harbor.k8s_environment.k8s_config.load_incluster_config",
        fake_incluster,
    )
    monkeypatch.setattr(
        "ridges_harbor.k8s_environment.k8s_config.load_kube_config",
        fake_kubeconfig,
    )

    core, batch = build_isolated_k8s_apis("my-context")
    try:
        assert kube_configs[0] is core.api_client.configuration
        assert core.api_client is batch.api_client
    finally:
        core.api_client.close()
