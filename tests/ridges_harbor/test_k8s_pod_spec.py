from kubernetes import client as k8s_client

from ridges_harbor.k8s_environment import AGENT_IMAGE_ROLE, RidgesKubernetesEnvironment


def _make_env() -> RidgesKubernetesEnvironment:
    env = RidgesKubernetesEnvironment.__new__(RidgesKubernetesEnvironment)
    env.image = "registry.test/task:abc123"
    env.cpu_request = "1"
    env.memory_request = "1024Mi"
    env.ephemeral_storage_request = "1024Mi"
    env.memory_limit = None
    env.image_role = AGENT_IMAGE_ROLE
    env._proxy_container = lambda: k8s_client.V1Container(name="proxy")
    return env


def test_main_container_keeps_root_dac_capabilities_for_verifier() -> None:
    """The verifier runs as root over an agent-owned worktree on non-root tasks;
    without DAC_OVERRIDE/FOWNER/CHOWN a capability-stripped root cannot reset it."""
    main = _make_env()._build_containers()[0]

    capabilities = main.security_context.capabilities
    assert capabilities.drop == ["ALL"]
    assert set(capabilities.add) == {"SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "CHOWN"}
