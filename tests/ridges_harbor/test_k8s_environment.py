from ridges_harbor.k8s_environment import RidgesKubernetesEnvironment


def _proxy_container(inference_seed: int | None):
    environment = RidgesKubernetesEnvironment.__new__(RidgesKubernetesEnvironment)
    environment.proxy_image = "sandbox-proxy:test"
    environment.max_cost_usd = "9"
    environment.evaluation_run_id = "eval-run-test"
    environment.inference_seed = inference_seed
    environment.openrouter_sidecar_env = {
        "RIDGES_OPENROUTER_WORKSPACE_ID": "workspace-test",
    }

    return environment._proxy_container()


def _proxy_container_env(inference_seed: int | None) -> dict[str, str]:
    return {variable.name: variable.value for variable in _proxy_container(inference_seed).env}


def test_proxy_container_receives_inference_seed() -> None:
    assert _proxy_container_env(123) == {
        "MAX_COST_USD": "9",
        "EVALUATION_RUN_ID": "eval-run-test",
        "INFERENCE_SEED": "123",
        "OPENROUTER_WORKSPACE_ID": "workspace-test",
    }


def test_proxy_container_omits_unset_inference_seed() -> None:
    assert "INFERENCE_SEED" not in _proxy_container_env(None)


def test_proxy_container_waits_for_health_endpoint() -> None:
    container = _proxy_container(None)

    assert container.startup_probe.http_get.path == "/healthz"
    assert container.startup_probe.http_get.port == 8080
    assert container.startup_probe.failure_threshold == 20
    assert container.readiness_probe.http_get.path == "/healthz"
    assert container.readiness_probe.http_get.port == 8080
