"""Kubernetes-specific runtime helpers for the Ridges proxy-sidecar scaffold.

Replaces the Docker network-connect logic in ``docker_runtime.py`` for the
Kubernetes execution backend.  The single responsibility here is flipping the
``ridges.ai/phase`` Pod label from ``agent`` to ``verification`` so the
namespace-level NetworkPolicy allows full egress during the verifier phase,
and signalling the SNI router inside the proxy container to unlock passthrough.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from kubernetes import client as k8s_client

logger = logging.getLogger(__name__)

TrialHook = Callable[[Any], Awaitable[None]]


def build_k8s_verifier_egress_hook(
    *,
    namespace: str,
    core_api: k8s_client.CoreV1Api,
) -> TrialHook:
    """Return a Harbor ``on_verification_started`` hook that enables full egress.

    Two things happen when the hook fires:

    1. The Pod label ``ridges.ai/phase`` is patched from ``"agent"`` to
       ``"verification"``.  Combined with the namespace-level NetworkPolicies
       (``ridges-agent-egress`` restricts to port 443; ``ridges-verification-egress``
       allows all egress), this gives the verifier unrestricted outbound access.

    2. ``touch /tmp/egress-unlocked`` is exec'd into the ``proxy`` container so
       the SNI router switches from allowlist-only mode to full passthrough.

    The pod name is derived from ``event.trial_id`` at invocation time because
    Harbor auto-generates the trial name (and thus the pod name via session_id)
    independently of the job name used by the runner.
    """

    async def enable_verifier_egress(event: Any) -> None:
        pod_name = event.trial_id.lower().replace("_", "-")[:63]

        # 1. Flip the NetworkPolicy label.
        await asyncio.to_thread(
            core_api.patch_namespaced_pod,
            name=pod_name,
            namespace=namespace,
            body={"metadata": {"labels": {"ridges.ai/phase": "verification"}}},
        )

        # 2. Signal the SNI router to unlock passthrough for all egress.
        try:
            from kubernetes.stream import stream as k8s_stream

            await asyncio.to_thread(
                k8s_stream,
                core_api.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=namespace,
                container="proxy",
                command=["touch", "/tmp/egress-unlocked"],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
            )
        except Exception as exc:
            # Non-fatal: the NetworkPolicy label flip already grants egress at
            # the network layer.  Log and continue so verification is not blocked.
            logger.warning("Failed to touch egress-unlocked sentinel in pod %s: %s", pod_name, exc)

    return enable_verifier_egress
