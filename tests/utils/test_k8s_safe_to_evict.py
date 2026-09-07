from __future__ import annotations

from unittest.mock import MagicMock

from kubernetes.client.rest import ApiException

from utils.k8s import SAFE_TO_EVICT_ANNOTATION, set_screener_safe_to_evict


def _kubernetes_env(monkeypatch, *, pod_name: str = "ridges-screener-1-0", namespace: str = "ridges-prod"):
    monkeypatch.setenv("RIDGES_ENVIRONMENT_TYPE", "kubernetes")
    monkeypatch.setenv("MY_POD_NAME", pod_name)
    monkeypatch.setenv("K8S_NAMESPACE", namespace)


def test_patches_false_and_true(monkeypatch):
    _kubernetes_env(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr("utils.k8s._get_core_api", lambda: api)

    set_screener_safe_to_evict(False)
    set_screener_safe_to_evict(True)

    assert api.patch_namespaced_pod.call_count == 2
    false_call, true_call = api.patch_namespaced_pod.call_args_list
    assert false_call.kwargs["name"] == "ridges-screener-1-0"
    assert false_call.kwargs["namespace"] == "ridges-prod"
    assert false_call.kwargs["body"] == {"metadata": {"annotations": {SAFE_TO_EVICT_ANNOTATION: "false"}}}
    assert true_call.kwargs["body"] == {"metadata": {"annotations": {SAFE_TO_EVICT_ANNOTATION: "true"}}}


def test_noop_without_pod_name(monkeypatch):
    monkeypatch.setenv("RIDGES_ENVIRONMENT_TYPE", "kubernetes")
    monkeypatch.delenv("MY_POD_NAME", raising=False)
    api = MagicMock()
    monkeypatch.setattr("utils.k8s._get_core_api", lambda: api)

    set_screener_safe_to_evict(False)

    api.patch_namespaced_pod.assert_not_called()


def test_noop_outside_kubernetes(monkeypatch):
    monkeypatch.setenv("RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setenv("MY_POD_NAME", "ridges-screener-1-0")
    api = MagicMock()
    monkeypatch.setattr("utils.k8s._get_core_api", lambda: api)

    set_screener_safe_to_evict(False)

    api.patch_namespaced_pod.assert_not_called()


def test_api_exception_does_not_raise(monkeypatch):
    _kubernetes_env(monkeypatch)
    api = MagicMock()
    api.patch_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    monkeypatch.setattr("utils.k8s._get_core_api", lambda: api)

    set_screener_safe_to_evict(False)

    api.patch_namespaced_pod.assert_called_once()
