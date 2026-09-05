from unittest.mock import MagicMock

import pytest

from ridges_harbor.k8s_environment import sanitize_kubernetes_resource_name
from ridges_harbor.k8s_runtime import build_k8s_verifier_egress_hook


class _Event:
    def __init__(self, trial_name: str) -> None:
        self.trial_name = trial_name


@pytest.mark.anyio
async def test_verifier_egress_hook_uses_sanitized_trial_name_env() -> None:
    trial_name = "My_Task/v2"
    core_api = MagicMock()
    hook = build_k8s_verifier_egress_hook(namespace="ridges", core_api=core_api)

    await hook(_Event(trial_name))

    expected = sanitize_kubernetes_resource_name(f"{trial_name}__env")
    core_api.patch_namespaced_pod.assert_called_once()
    assert core_api.patch_namespaced_pod.call_args.kwargs["name"] == expected
    assert core_api.patch_namespaced_pod.call_args.kwargs["namespace"] == "ridges"


@pytest.mark.anyio
async def test_verifier_egress_hook_matches_environment_pod_name() -> None:
    trial_name = ("task-" * 20) + "first"
    core_api = MagicMock()
    hook = build_k8s_verifier_egress_hook(namespace="ridges", core_api=core_api)

    await hook(_Event(trial_name))

    patched_name = core_api.patch_namespaced_pod.call_args.kwargs["name"]
    assert patched_name == sanitize_kubernetes_resource_name(f"{trial_name}__env")
    assert len(patched_name) <= 63
