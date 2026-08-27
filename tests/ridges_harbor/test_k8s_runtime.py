from unittest.mock import MagicMock

import pytest

from ridges_harbor.k8s_environment import sanitize_kubernetes_resource_name
from ridges_harbor.k8s_runtime import build_k8s_verifier_egress_hook


class _Event:
    def __init__(self, trial_id: str) -> None:
        self.trial_id = trial_id


@pytest.mark.anyio
async def test_verifier_egress_hook_uses_sanitized_trial_id() -> None:
    trial_id = "My_Task/v2__ABC123"
    core_api = MagicMock()
    hook = build_k8s_verifier_egress_hook(namespace="ridges", core_api=core_api)

    await hook(_Event(trial_id))

    expected = sanitize_kubernetes_resource_name(trial_id)
    core_api.patch_namespaced_pod.assert_called_once()
    assert core_api.patch_namespaced_pod.call_args.kwargs["name"] == expected
    assert core_api.patch_namespaced_pod.call_args.kwargs["namespace"] == "ridges"


@pytest.mark.anyio
async def test_verifier_egress_hook_matches_environment_pod_name() -> None:
    trial_id = ("task-" * 20) + "first"
    core_api = MagicMock()
    hook = build_k8s_verifier_egress_hook(namespace="ridges", core_api=core_api)

    await hook(_Event(trial_id))

    patched_name = core_api.patch_namespaced_pod.call_args.kwargs["name"]
    assert patched_name == sanitize_kubernetes_resource_name(trial_id)
    assert len(patched_name) <= 63
