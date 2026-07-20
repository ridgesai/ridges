"""Kubernetes utility helpers — parallel to utils/docker.py."""

from __future__ import annotations

import logging
import os
from typing import Optional

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

_core_api: Optional[k8s_client.CoreV1Api] = None


def _get_core_api() -> k8s_client.CoreV1Api:
    global _core_api
    if _core_api is None:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config(context=os.getenv("K8S_CONTEXT"))
        _core_api = k8s_client.CoreV1Api()
    return _core_api


def get_num_k8s_eval_pods() -> int:
    """Count running eval Pods (app=ridges-eval) in the namespace."""
    namespace = os.getenv("K8S_NAMESPACE", "ridges")
    api = _get_core_api()
    pods = api.list_namespaced_pod(
        namespace=namespace,
        label_selector="app=ridges-eval",
        field_selector="status.phase=Running",
    )
    return len(pods.items)


def cleanup_harbor_k8s_resources() -> None:
    """Delete orphaned eval Pods at startup.

    Safe in multi-screener deployments: only deletes pods owned by this
    screener (stale UID) or pods with no owner reference (legacy).
    """
    namespace = os.getenv("K8S_NAMESPACE", "ridges")
    my_pod_name = os.getenv("MY_POD_NAME")
    my_pod_uid = os.getenv("MY_POD_UID")
    api = _get_core_api()

    logger.info("Cleaning up stale K8s eval Pods...")

    pods = api.list_namespaced_pod(
        namespace=namespace,
        label_selector="app=ridges-eval",
    )

    removed = 0
    for pod in pods.items:
        owner_refs = pod.metadata.owner_references or []

        if not owner_refs:
            # Legacy pod with no owner reference -- always clean up.
            pass
        elif my_pod_name and my_pod_uid:
            # Delete only if an owner ref points to this screener with a stale UID.
            dominated_by_us = any(ref.name == my_pod_name and ref.uid != my_pod_uid for ref in owner_refs)
            if not dominated_by_us:
                continue  # Owned by a different screener, or current UID matches.
        else:
            # No Downward API vars available (local dev?) -- skip pods with owners.
            continue

        pod_name = pod.metadata.name
        logger.info(f"Removing stale eval Pod {pod_name}...")
        try:
            api.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                body=k8s_client.V1DeleteOptions(
                    grace_period_seconds=0,
                    propagation_policy="Background",
                ),
            )
            removed += 1
        except ApiException as exc:
            if exc.status != 404:
                logger.warning(f"Failed to remove stale eval Pod {pod_name}: {exc}")

    logger.info(f"Removed {removed} stale eval Pod(s)")


def cleanup_completed_k8s_eval_pods() -> None:
    """Delete Succeeded/Failed eval Pods after an evaluation batch.

    Lighter than the startup sweep -- only targets non-Running pods.
    """
    namespace = os.getenv("K8S_NAMESPACE", "ridges")
    my_pod_name = os.getenv("MY_POD_NAME")
    api = _get_core_api()

    for phase in ("Succeeded", "Failed"):
        try:
            pods = api.list_namespaced_pod(
                namespace=namespace,
                label_selector="app=ridges-eval",
                field_selector=f"status.phase={phase}",
            )
        except ApiException:
            continue

        for pod in pods.items:
            owner_refs = pod.metadata.owner_references or []
            is_ours = not owner_refs or (my_pod_name and any(ref.name == my_pod_name for ref in owner_refs))
            if not is_ours:
                continue

            try:
                api.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=namespace,
                    body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
                )
            except ApiException:
                pass
