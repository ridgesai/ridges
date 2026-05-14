"""Pre-build the proxy sidecar image before dispatching evaluation runs.

This module exposes a single async function, ``ensure_proxy_image()``, that
can be called once in ``_run_evaluation()`` before any eval tasks are
dispatched. Building the proxy once avoids N identical Kaniko jobs (and N
identical error tracebacks) when multiple eval runs share the same proxy
version.

The logic here mirrors ``RidgesKubernetesEnvironment._ensure_proxy_image()``
and its helpers, but operates without requiring a full environment instance.
"""

from __future__ import annotations

import asyncio
import base64
import http.client
import urllib.parse

import utils.logger as logger
from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes import config as k8s_config  # type: ignore[import-untyped]
from kubernetes.client.exceptions import ApiException  # type: ignore[import-untyped]


def _init_k8s_clients(context: str | None) -> tuple[k8s_client.CoreV1Api, k8s_client.BatchV1Api]:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config(context=context)
    return k8s_client.CoreV1Api(), k8s_client.BatchV1Api()


def _image_exists(registry: str, tag: str, *, password: str | None) -> bool:
    parsed = urllib.parse.urlsplit(f"http://{registry}")
    host = parsed.hostname or registry.split(":")[0]
    port = parsed.port or 5000
    try:
        conn = http.client.HTTPConnection(host, port, timeout=10)
        headers: dict[str, str] = {}
        if password:
            cred = base64.b64encode(b"kaniko:" + password.encode()).decode()
            headers["Authorization"] = f"Basic {cred}"
        conn.request("HEAD", f"/v2/sandbox-proxy/manifests/{tag}", headers=headers)
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except Exception as exc:
        logger.debug(f"[k8s_prebuild] Registry check failed for sandbox-proxy:{tag}: {exc}")
        return False


def _create_secret_sync(
    api: k8s_client.CoreV1Api,
    secret_name: str,
    namespace: str,
    presigned_url: str,
) -> None:
    secret = k8s_client.V1Secret(
        metadata=k8s_client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            labels={"ridges.ai/managed-by": "screener", "ridges.ai/build-url": "true"},
        ),
        string_data={"url": presigned_url},
    )
    api.create_namespaced_secret(namespace=namespace, body=secret)


def _create_job_sync(
    batch: k8s_client.BatchV1Api,
    job_name: str,
    secret_name: str,
    image_ref: str,
    namespace: str,
    registry: str,
    registry_insecure: bool,
    registry_credentials_secret: str | None,
) -> None:
    init_container = k8s_client.V1Container(
        name="fetch-context",
        image="curlimages/curl:latest",
        command=["/bin/sh", "-c"],
        args=[
            'curl -sSfL "$PRESIGNED_URL" -o /tmp/proxy.tar.gz && '
            "mkdir -p /workspace && "
            "tar -xzf /tmp/proxy.tar.gz -C /workspace && "
            "test -f /workspace/Dockerfile"
        ],
        env=[
            k8s_client.V1EnvVar(
                name="PRESIGNED_URL",
                value_from=k8s_client.V1EnvVarSource(
                    secret_key_ref=k8s_client.V1SecretKeySelector(
                        name=secret_name,
                        key="url",
                    )
                ),
            )
        ],
        volume_mounts=[k8s_client.V1VolumeMount(name="context", mount_path="/workspace")],
    )

    kaniko_args = [
        "--dockerfile=Dockerfile",
        "--context=dir:///workspace",
        f"--destination={image_ref}",
        "--cache=true",
        f"--cache-repo={registry}/cache",
    ]
    if registry_insecure:
        kaniko_args.append(f"--insecure-registry={registry}")

    kaniko_volume_mounts = [k8s_client.V1VolumeMount(name="context", mount_path="/workspace")]
    if registry_credentials_secret:
        kaniko_volume_mounts.append(
            k8s_client.V1VolumeMount(
                name="docker-config",
                mount_path="/kaniko/.docker",
                read_only=True,
            )
        )

    kaniko_container = k8s_client.V1Container(
        name="kaniko",
        image="gcr.io/kaniko-project/executor:latest",
        args=kaniko_args,
        volume_mounts=kaniko_volume_mounts,
    )

    volumes = [
        k8s_client.V1Volume(
            name="context",
            empty_dir=k8s_client.V1EmptyDirVolumeSource(size_limit="200Mi"),
        )
    ]
    if registry_credentials_secret:
        volumes.append(
            k8s_client.V1Volume(
                name="docker-config",
                secret=k8s_client.V1SecretVolumeSource(
                    secret_name=registry_credentials_secret,
                    items=[
                        k8s_client.V1KeyToPath(
                            key=".dockerconfigjson",
                            path="config.json",
                        )
                    ],
                ),
            )
        )

    pod_labels = {"ridges.ai/managed-by": "screener", "ridges.ai/build-job": "true"}
    job = k8s_client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s_client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=pod_labels,
        ),
        spec=k8s_client.V1JobSpec(
            backoff_limit=2,
            ttl_seconds_after_finished=300,
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(labels=pod_labels),
                spec=k8s_client.V1PodSpec(
                    init_containers=[init_container],
                    containers=[kaniko_container],
                    restart_policy="Never",
                    volumes=volumes,
                ),
            ),
        ),
    )
    batch.create_namespaced_job(namespace=namespace, body=job)


async def _delete_secret(api: k8s_client.CoreV1Api, secret_name: str, namespace: str) -> None:
    try:
        await asyncio.to_thread(api.delete_namespaced_secret, name=secret_name, namespace=namespace)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning(f"[k8s_prebuild] Failed to delete Secret {secret_name}: {exc}")


async def _delete_job(
    batch: k8s_client.BatchV1Api, job_name: str, namespace: str
) -> None:
    try:
        await asyncio.to_thread(
            batch.delete_namespaced_job,
            name=job_name,
            namespace=namespace,
            body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
        )
        await asyncio.sleep(2)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning(f"[k8s_prebuild] Failed to delete job {job_name}: {exc}")


async def _is_job_failed(batch: k8s_client.BatchV1Api, job_name: str, namespace: str) -> bool:
    try:
        job = await asyncio.to_thread(
            batch.read_namespaced_job, name=job_name, namespace=namespace
        )
        for cond in job.status.conditions or []:
            if cond.type == "Failed" and cond.status == "True":
                return True
        return False
    except ApiException:
        return False


async def _wait_for_job(
    batch: k8s_client.BatchV1Api,
    api: k8s_client.CoreV1Api,
    job_name: str,
    secret_name: str,
    namespace: str,
    timeout_sec: int = 300,
) -> None:
    logger.debug(f"[k8s_prebuild] Waiting for proxy build job {job_name} (timeout={timeout_sec}s)")
    deadline = asyncio.get_event_loop().time() + timeout_sec

    while asyncio.get_event_loop().time() < deadline:
        try:
            job = await asyncio.to_thread(
                batch.read_namespaced_job, name=job_name, namespace=namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                await asyncio.sleep(5)
                continue
            raise

        for cond in job.status.conditions or []:
            if cond.type == "Complete" and cond.status == "True":
                logger.info(f"[k8s_prebuild] Proxy build job {job_name} completed successfully")
                await _delete_secret(api, secret_name, namespace)
                return
            if cond.type == "Failed" and cond.status == "True":
                await _delete_secret(api, secret_name, namespace)
                raise RuntimeError(f"Proxy build job {job_name} failed: {cond.message}")

        await asyncio.sleep(5)

    await _delete_secret(api, secret_name, namespace)
    raise RuntimeError(f"Proxy build job {job_name} did not complete within {timeout_sec}s")


async def ensure_proxy_image(
    proxy_version: str,
    proxy_source_url: str,
) -> None:
    """Ensure sandbox-proxy:{proxy_version} exists in the in-cluster registry.

    Builds the image via a Kaniko Job if it is missing. Handles concurrent
    builds (409 conflict) by waiting for the existing job to complete.

    Raises ``RuntimeError`` if the build fails so the caller can surface one
    consolidated error rather than N per-eval tracebacks.
    """
    import validator.config as config

    registry = config.K8S_REGISTRY
    namespace = config.K8S_NAMESPACE
    context = config.K8S_CONTEXT
    registry_credentials_secret = config.K8S_REGISTRY_SECRET
    registry_password = config.K8S_REGISTRY_PASSWORD
    registry_insecure = config.K8S_REGISTRY_INSECURE

    api, batch = await asyncio.to_thread(_init_k8s_clients, context)

    proxy_ref = f"{registry}/sandbox-proxy:{proxy_version}"

    exists = await asyncio.to_thread(
        _image_exists, registry, proxy_version, password=registry_password
    )
    if exists:
        logger.debug(f"[k8s_prebuild] Proxy image {proxy_ref} already in registry – skipping build")
        return

    job_name = f"build-proxy-{proxy_version}"
    secret_name = f"{job_name}-url"
    logger.info(f"[k8s_prebuild] Pre-building proxy image {proxy_ref}")

    try:
        await asyncio.to_thread(_create_secret_sync, api, secret_name, namespace, proxy_source_url)
        await asyncio.to_thread(
            _create_job_sync,
            batch,
            job_name,
            secret_name,
            proxy_ref,
            namespace,
            registry,
            registry_insecure,
            registry_credentials_secret,
        )
    except ApiException as exc:
        if exc.status == 409:
            if await _is_job_failed(batch, job_name, namespace):
                logger.warning(
                    f"[k8s_prebuild] Proxy build job {job_name} previously failed — retrying"
                )
                await _delete_job(batch, job_name, namespace)
                await _delete_secret(api, secret_name, namespace)
                await asyncio.to_thread(_create_secret_sync, api, secret_name, namespace, proxy_source_url)
                await asyncio.to_thread(
                    _create_job_sync,
                    batch,
                    job_name,
                    secret_name,
                    proxy_ref,
                    namespace,
                    registry,
                    registry_insecure,
                    registry_credentials_secret,
                )
            else:
                logger.debug(
                    f"[k8s_prebuild] Proxy build job {job_name} already running – waiting"
                )
        else:
            raise

    await _wait_for_job(batch, api, job_name, secret_name, namespace)
