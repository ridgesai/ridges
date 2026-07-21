"""Kubernetes-backed Harbor environments for the Ridges screener.

Two classes are defined here:

* ``KubernetesEnvironment`` – generic, cloud-agnostic Harbor environment that
  runs each trial inside a Kubernetes Pod.  It has no Ridges-specific logic and
  could eventually be contributed upstream to Harbor.

* ``RidgesKubernetesEnvironment`` – subclass that adds:
  - On-demand image building via Kaniko + an in-cluster ``registry:2`` (no
    pre-building, no shared PVCs; build context is fetched from S3).
  - Proxy sidecar container (MITM SSL, cost tracking, OpenRouter allow-list).
  - iptables init container (``NET_ADMIN``) to transparently redirect port-443
    traffic to the proxy SNI router on port 15443 (UID 1337 is exempt so the
    proxy can reach the internet directly).
  - Phase labels for NetworkPolicy-based egress isolation.
  - ``proxy-certs`` / ``proxy-data`` emptyDir volumes.
  - ``stop()`` override that downloads ``/data`` from the Pod before deletion
    so that proxy cost data is available for reporting.
"""

from __future__ import annotations

import asyncio
import io
import re
import shlex
import tarfile
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.environment_type import EnvironmentType
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from tenacity import retry, stop_after_attempt, wait_exponential

# ---------------------------------------------------------------------------
# KubernetesEnvironment – generic base
# ---------------------------------------------------------------------------


class KubernetesEnvironment(BaseEnvironment):
    """Generic Kubernetes environment for Harbor.

    Works with any cluster accessible via a kubeconfig context or from inside
    the cluster (in-cluster service-account config).  No cloud-provider
    dependencies.
    """

    def __init__(
        self,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        *,
        namespace: str = "default",
        image: str | None = None,
        kubeconfig_context: str | None = None,
        node_selector: dict[str, str] | None = None,
        service_account_name: str | None = None,
        memory_limit_multiplier: float | None = 1.0,
        memory_request_fraction: float = 1.0,
        cpu_request_fraction: float = 1.0,
        labels: dict[str, str] | None = None,
        image_pull_secrets: list[str] | None = None,
        owner_pod_name: str | None = None,
        owner_pod_uid: str | None = None,
        **kwargs,
    ):
        super().__init__(
            environment_dir=environment_dir,
            environment_name=environment_name,
            session_id=session_id,
            trial_paths=trial_paths,
            task_env_config=task_env_config,
            **kwargs,
        )

        self.namespace = namespace
        self.image = image or f"{environment_name}:latest"
        self.kubeconfig_context = kubeconfig_context
        self.node_selector = node_selector
        self.service_account_name = service_account_name
        self._extra_labels: dict[str, str] = labels or {}
        self._image_pull_secrets: list[str] = image_pull_secrets or []
        self._owner_pod_name = owner_pod_name
        self._owner_pod_uid = owner_pod_uid

        # Resource sizing.
        self.cpu_request = str(round(max(task_env_config.cpus * cpu_request_fraction, 0.1), 3))
        self.memory_request = f"{max(int(task_env_config.memory_mb * memory_request_fraction), 128)}Mi"
        self.ephemeral_storage_request = f"{task_env_config.storage_mb}Mi"

        if memory_limit_multiplier is not None and memory_limit_multiplier > 0:
            self.memory_limit: str | None = f"{int(task_env_config.memory_mb * memory_limit_multiplier)}Mi"
        else:
            self.memory_limit = None

        # Pod name: lowercase, no underscores, max 63 chars
        self.pod_name = session_id.lower().replace("_", "-")[:63]

        # Kubernetes client – lazily initialised
        self._core_api: k8s_client.CoreV1Api | None = None
        self._batch_api: k8s_client.BatchV1Api | None = None

    # ------------------------------------------------------------------
    # BaseEnvironment abstract properties
    # ------------------------------------------------------------------

    @staticmethod
    def type() -> EnvironmentType:
        # There is no canonical EnvironmentType for generic k8s; return GKE so
        # Harbor's existing type-enum machinery doesn't break.  We always reach
        # this class via import_path, not by type, so the value is never used
        # for routing.
        return EnvironmentType.GKE

    @property
    def is_mounted(self) -> bool:
        return False

    @property
    def supports_gpus(self) -> bool:
        return False

    @property
    def can_disable_internet(self) -> bool:
        # Internet isolation is handled externally via NetworkPolicies.
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_definition(self) -> None:
        """No local file validation needed – the image lives in the registry."""

    def _init_k8s_client(self) -> None:
        """Load kubeconfig (or in-cluster config) and create API clients."""
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config(context=self.kubeconfig_context)
        self._core_api = k8s_client.CoreV1Api()
        self._batch_api = k8s_client.BatchV1Api()

    async def _ensure_client(self) -> None:
        if self._core_api is None:
            await asyncio.to_thread(self._init_k8s_client)

    @property
    def _api(self) -> k8s_client.CoreV1Api:
        if self._core_api is None:
            raise RuntimeError("Kubernetes client not initialised. Call _ensure_client() first.")
        return self._core_api

    @property
    def _batch(self) -> k8s_client.BatchV1Api:
        if self._batch_api is None:
            raise RuntimeError("Kubernetes batch client not initialised. Call _ensure_client() first.")
        return self._batch_api

    # ------------------------------------------------------------------
    # Extension points (overrideable by subclasses)
    # ------------------------------------------------------------------

    def _build_labels(self) -> dict[str, str]:
        base = {
            "app": "ridges-eval",
            "session": self.session_id[:63],
        }
        base.update(self._extra_labels)
        return base

    def _build_volumes(self) -> list[k8s_client.V1Volume]:
        return []

    def _build_containers(self) -> list[k8s_client.V1Container]:
        requests: dict[str, str] = {
            "cpu": self.cpu_request,
            "memory": self.memory_request,
        }
        if self.ephemeral_storage_request:
            requests["ephemeral-storage"] = self.ephemeral_storage_request

        limits: dict[str, str] = {}
        if self.memory_limit:
            limits["memory"] = self.memory_limit

        return [
            k8s_client.V1Container(
                name="main",
                image=self.image,
                command=["sleep", "infinity"],
                resources=k8s_client.V1ResourceRequirements(
                    requests=requests,
                    limits=limits or None,
                ),
                volume_mounts=[],
            )
        ]

    def _build_pod_spec(self) -> k8s_client.V1PodSpec:
        spec = k8s_client.V1PodSpec(
            containers=self._build_containers(),
            volumes=self._build_volumes() or None,
            restart_policy="Never",
        )
        if self.node_selector:
            spec.node_selector = self.node_selector
        if self.service_account_name:
            spec.service_account_name = self.service_account_name
        if self._image_pull_secrets:
            spec.image_pull_secrets = [k8s_client.V1LocalObjectReference(name=s) for s in self._image_pull_secrets]
        return spec

    def _build_pod(self) -> k8s_client.V1Pod:
        metadata = k8s_client.V1ObjectMeta(
            name=self.pod_name,
            namespace=self.namespace,
            labels=self._build_labels(),
        )
        if self._owner_pod_name and self._owner_pod_uid:
            metadata.owner_references = [
                k8s_client.V1OwnerReference(
                    api_version="v1",
                    kind="Pod",
                    name=self._owner_pod_name,
                    uid=self._owner_pod_uid,
                    block_owner_deletion=True,
                    controller=False,
                ),
            ]
        return k8s_client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=metadata,
            spec=self._build_pod_spec(),
        )

    # ------------------------------------------------------------------
    # BaseEnvironment lifecycle
    # ------------------------------------------------------------------

    async def start(self, force_build: bool) -> None:
        """Create the Pod and wait until all containers are ready."""
        await self._ensure_client()

        pod = self._build_pod()

        try:
            await asyncio.to_thread(
                self._api.create_namespaced_pod,
                namespace=self.namespace,
                body=pod,
            )
        except ApiException as exc:
            if exc.status == 409:
                self.logger.debug(f"Pod {self.pod_name} already exists – deleting and recreating")
                await self._delete_pod_and_wait()
                await asyncio.to_thread(
                    self._api.create_namespaced_pod,
                    namespace=self.namespace,
                    body=pod,
                )
            else:
                raise RuntimeError(f"Failed to create Pod {self.pod_name}: {exc}") from exc

        await self._wait_for_pod_ready()

        mkdir_result = await self.exec(
            f"mkdir -p {EnvironmentPaths.agent_dir} {EnvironmentPaths.verifier_dir} && "
            f"chmod 777 {EnvironmentPaths.agent_dir} {EnvironmentPaths.verifier_dir}"
        )
        if mkdir_result.return_code != 0:
            raise RuntimeError(
                f"Failed to create log directories in Pod {self.pod_name}: "
                f"stdout={mkdir_result.stdout}, stderr={mkdir_result.stderr}"
            )

    async def stop(self, delete: bool = True) -> None:
        """Delete the Pod (optionally)."""
        if self._core_api is None:
            return
        if delete:
            try:
                await self._delete_pod_and_wait()
            except RuntimeError:
                self.logger.warning(f"Pod {self.pod_name} did not terminate cleanly during stop()")

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Execute *command* inside the main container of the Pod."""
        user = self._resolve_user(user)
        env = self._merge_env(env)

        await self._ensure_client()

        full_command = f"bash -c {shlex.quote(command)}"

        if env:
            prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            full_command = f"{prefix} {full_command}"

        if cwd:
            full_command = f"cd {shlex.quote(cwd)} && {full_command}"

        if user is not None:
            if isinstance(user, int):
                user_arg = f"$(getent passwd {user} | cut -d: -f1)"
            else:
                user_arg = shlex.quote(str(user))
            full_command = f"su {user_arg} -s /bin/bash -c {shlex.quote(full_command)}"

        exec_command = ["sh", "-c", full_command]

        resp = None
        try:
            resp = await asyncio.to_thread(
                stream,
                self._api.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container="main",
                command=exec_command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
            )

            if timeout_sec is not None:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.to_thread(self._read_exec_output, resp),
                    timeout=timeout_sec,
                )
            else:
                stdout, stderr = await asyncio.to_thread(self._read_exec_output, resp)

            resp.run_forever(timeout=0)
            return_code = resp.returncode if resp.returncode is not None else 0
            return ExecResult(stdout=stdout, stderr=stderr, return_code=return_code)

        except asyncio.TimeoutError:
            return ExecResult(
                stdout=None,
                stderr=f"Command timed out after {timeout_sec} seconds",
                return_code=124,
            )
        except ApiException as exc:
            if exc.status == 404:
                return ExecResult(stdout=None, stderr=f"Pod {self.pod_name} not found (404)", return_code=1)
            return ExecResult(
                stdout=None,
                stderr=f"API error ({exc.status}) on Pod {self.pod_name}: {exc.reason}",
                return_code=1,
            )
        except Exception as exc:
            return ExecResult(stdout=None, stderr=str(exc), return_code=1)
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        await self._ensure_client()
        await self._wait_for_container_exec_ready()

        source_path = Path(source_path)
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            tar.add(str(source_path), arcname=Path(target_path).name)
        tar_buffer.seek(0)

        target_dir = str(Path(target_path).parent)
        await self.exec(f"mkdir -p {shlex.quote(target_dir)}", user="root")

        resp = await asyncio.to_thread(
            stream,
            self._api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.namespace,
            container="main",
            command=["tar", "xf", "-", "-C", target_dir],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        resp.write_stdin(tar_buffer.read())
        resp.run_forever(timeout=1)
        resp.close()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        await self._ensure_client()
        await self._wait_for_container_exec_ready()

        source_dir = Path(source_dir)
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for item in source_dir.rglob("*"):
                if item.is_file():
                    tar.add(str(item), arcname=str(item.relative_to(source_dir)))
        tar_buffer.seek(0)

        await self.exec(f"mkdir -p {shlex.quote(target_dir)}", user="root")

        resp = await asyncio.to_thread(
            stream,
            self._api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.namespace,
            container="main",
            command=["tar", "xf", "-", "-C", target_dir],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        try:
            resp.write_stdin(tar_buffer.read())
        except Exception as exc:
            raise RuntimeError(f"Failed to write tar data to Pod {self.pod_name}: {exc}") from exc
        resp.run_forever(timeout=1)
        resp.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        await self._ensure_client()
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        resp = await asyncio.to_thread(
            stream,
            self._api.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.namespace,
            container="main",
            command=["tar", "cf", "-", source_path],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        tar_data = b""
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                chunk = resp.read_stdout()
                tar_data += chunk.encode("utf-8", errors="surrogateescape") if isinstance(chunk, str) else chunk

        tar_buffer = io.BytesIO(tar_data)
        with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
            for member in tar.getmembers():
                if member.name == source_path or member.name.startswith(source_path.lstrip("/")):
                    member.name = target_path.name
                    tar.extract(member, path=str(target_path.parent))
                    break

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        await self._ensure_client()
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            resp = await asyncio.to_thread(
                stream,
                self._api.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container="main",
                command=["sh", "-c", f"cd {shlex.quote(source_dir)} && tar cf - ."],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
            )
        except ApiException as exc:
            raise RuntimeError(f"Failed to start tar download from Pod {self.pod_name}: {exc}") from exc

        tar_data = b""
        stderr_data = ""
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                chunk = resp.read_stdout()
                tar_data += chunk.encode("utf-8", errors="surrogateescape") if isinstance(chunk, str) else chunk
            if resp.peek_stderr():
                stderr_data += resp.read_stderr()

        if stderr_data and ("No such file or directory" in stderr_data or "cannot cd" in stderr_data):
            raise RuntimeError(f"Failed to access directory {source_dir} in Pod {self.pod_name}: {stderr_data.strip()}")

        if not tar_data:
            raise RuntimeError(f"No data received when downloading {source_dir} from Pod {self.pod_name}")

        tar_buffer = io.BytesIO(tar_data)
        try:
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                tar.extractall(path=str(target_dir))
        except tarfile.TarError as exc:
            raise RuntimeError(f"Failed to extract {source_dir} from Pod {self.pod_name}: {exc}") from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_exec_output(self, resp: Any) -> tuple[str, str]:
        stdout = ""
        stderr = ""
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                stdout += resp.read_stdout()
            if resp.peek_stderr():
                stderr += resp.read_stderr()
        return stdout, stderr

    async def _wait_for_container_exec_ready(self, max_attempts: int = 60) -> None:
        for attempt in range(max_attempts):
            try:
                resp = await asyncio.to_thread(
                    stream,
                    self._api.connect_get_namespaced_pod_exec,
                    self.pod_name,
                    self.namespace,
                    container="main",
                    command=["true"],
                    stderr=False,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                )
                resp.close()
                return
            except ApiException as exc:
                if "container not found" in str(exc) or exc.status == 500:
                    if attempt % 10 == 0:
                        self.logger.debug(f"Container not ready, attempt {attempt + 1}/{max_attempts}")
                    await asyncio.sleep(3)
                else:
                    raise
            except Exception:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(3)
                else:
                    raise
        raise RuntimeError(f"Container not ready for exec after {max_attempts} attempts")

    async def _wait_for_pod_ready(self, timeout_sec: int = 600) -> None:
        self.logger.debug(f"Waiting for Pod {self.pod_name} to be ready...")
        for attempt in range(timeout_sec):
            try:
                pod = await asyncio.to_thread(
                    self._api.read_namespaced_pod,
                    name=self.pod_name,
                    namespace=self.namespace,
                )
                phase = pod.status.phase
                if phase == "Running" and pod.status.container_statuses:
                    if all(c.ready for c in pod.status.container_statuses):
                        self.logger.debug(f"Pod {self.pod_name} is ready")
                        return
                elif phase in ("Failed", "Unknown", "Error"):
                    raise RuntimeError(f"Pod {self.pod_name} failed to start: {self._pod_failure_summary(pod)}")
                elif phase == "Pending" and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.state.waiting and cs.state.waiting.reason in ("ImagePullBackOff", "ErrImagePull"):
                            raise RuntimeError(
                                f"Failed to pull image for Pod {self.pod_name}: {cs.state.waiting.message}"
                            )
            except ApiException as exc:
                if exc.status != 404:
                    raise RuntimeError(f"Kubernetes API error while waiting for Pod: {exc}") from exc

            if attempt % 10 == 0:
                self.logger.debug(f"Pod {self.pod_name} not ready yet ({attempt}s elapsed)")
            await asyncio.sleep(1)

        raise RuntimeError(f"Pod {self.pod_name} not ready after {timeout_sec}s")

    async def _delete_pod_and_wait(self, timeout_sec: int = 60) -> None:
        try:
            await asyncio.to_thread(
                self._api.delete_namespaced_pod,
                name=self.pod_name,
                namespace=self.namespace,
                body=k8s_client.V1DeleteOptions(grace_period_seconds=0, propagation_policy="Foreground"),
            )
        except ApiException as exc:
            if exc.status == 404:
                return
            raise

        for _ in range(timeout_sec):
            try:
                await asyncio.to_thread(self._api.read_namespaced_pod, name=self.pod_name, namespace=self.namespace)
                await asyncio.sleep(1)
            except ApiException as exc:
                if exc.status == 404:
                    return

        self.logger.warning(f"Pod {self.pod_name} did not terminate within {timeout_sec}s")

    def _pod_failure_summary(self, pod: Any) -> str:
        parts: list[str] = []
        if pod.status.reason:
            parts.append(f"reason={pod.status.reason}")
        if pod.status.message:
            parts.append(f"message={pod.status.message}")
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state.waiting:
                    parts.append(f"container {cs.name} waiting: {cs.state.waiting.reason}")
                elif cs.state.terminated:
                    parts.append(f"container {cs.name} terminated: exit={cs.state.terminated.exit_code}")
        return "; ".join(parts) or "unknown"


# ---------------------------------------------------------------------------
# RidgesKubernetesEnvironment – adds proxy sidecar + on-demand Kaniko builds
# ---------------------------------------------------------------------------


class RidgesKubernetesEnvironment(KubernetesEnvironment):
    """Kubernetes environment with the Ridges proxy sidecar and on-demand image building.

    Image building flow (on cache miss):
    1. Screener creates a Kubernetes Secret with the S3 presigned URL.
    2. A Kaniko Job is created; its init container downloads the task archive
       from S3 and extracts it to an emptyDir.
    3. Kaniko builds the image and pushes it to the in-cluster ``registry:2``.
    4. On success, the Pod is created to run the evaluation.

    Proxy data flow:
    - The proxy sidecar writes cost/usage data to ``/data`` (emptyDir volume).
    - ``stop()`` downloads ``/data`` from the Pod to ``proxy_data_dir`` on the
      host before deleting the Pod so cost reporting still works.
    """

    def __init__(
        self,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        *,
        registry: str,
        task_name: str,
        digest_tag: str,
        task_archive_presigned_url: str,
        proxy_image: str,
        evaluation_run_id: str,
        max_cost_usd: str = "999999",
        openrouter_sidecar_env: dict[str, str] | None = None,
        proxy_data_dir: str | Path | None = None,
        registry_credentials_secret: str | None = None,
        registry_password: str | None = None,
        registry_insecure: bool = True,
        **kwargs,
    ):
        self.registry = registry
        self.task_name = task_name
        self.digest_tag = digest_tag
        self.task_archive_presigned_url = task_archive_presigned_url
        self.proxy_image = proxy_image
        self.evaluation_run_id = evaluation_run_id
        self.max_cost_usd = max_cost_usd
        self.openrouter_sidecar_env: dict[str, str] = openrouter_sidecar_env or {}
        self.proxy_data_dir: Path | None = Path(proxy_data_dir) if proxy_data_dir else None
        self.registry_credentials_secret = registry_credentials_secret
        self._registry_password = registry_password
        self._registry_insecure = registry_insecure

        image = f"{registry}/{task_name}:{digest_tag}"
        # Eval pods need credentials to pull the task image from the in-cluster registry.
        pull_secrets = [registry_credentials_secret] if registry_credentials_secret else []
        super().__init__(
            environment_dir=environment_dir,
            environment_name=environment_name,
            session_id=session_id,
            trial_paths=trial_paths,
            task_env_config=task_env_config,
            image=image,
            image_pull_secrets=pull_secrets,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, force_build: bool) -> None:
        """Ensure the task image exists in the registry, then start the Pod."""
        await self._ensure_client()
        await self._ensure_image(force_build=force_build)
        await super().start(force_build=False)

    async def stop(self, delete: bool = True) -> None:
        """Download proxy data from the Pod, then delete it."""
        if self.proxy_data_dir is not None and self._core_api is not None:
            try:
                self.logger.debug(f"Downloading proxy data from Pod {self.pod_name}:/proxy-data")
                await self.download_dir("/proxy-data", self.proxy_data_dir)
            except Exception as exc:
                self.logger.warning(f"Failed to download proxy data from Pod {self.pod_name}: {exc}")
        await super().stop(delete=delete)

    # ------------------------------------------------------------------
    # Pod spec overrides
    # ------------------------------------------------------------------

    def _build_labels(self) -> dict[str, str]:
        labels = super()._build_labels()
        labels["ridges.ai/phase"] = "agent"
        labels["ridges.ai/evaluation-run-id"] = self.evaluation_run_id
        return labels

    def _build_volumes(self) -> list[k8s_client.V1Volume]:
        volumes = super()._build_volumes()
        volumes.append(k8s_client.V1Volume(name="proxy-certs", empty_dir=k8s_client.V1EmptyDirVolumeSource()))
        volumes.append(k8s_client.V1Volume(name="proxy-data", empty_dir=k8s_client.V1EmptyDirVolumeSource()))
        return volumes

    def _build_containers(self) -> list[k8s_client.V1Container]:
        containers = super()._build_containers()

        # Point the main container at the CA bundle written by the proxy entrypoint.
        # ca-bundle.crt = system CAs + proxy CA, so agents trust both external
        # HTTPS endpoints and the proxy's self-signed cert.
        main = containers[0]
        main_env = list(main.env or [])
        main_env.append(k8s_client.V1EnvVar(name="SSL_CERT_FILE", value="/proxy-certs/ca-bundle.crt"))
        main_env.append(k8s_client.V1EnvVar(name="REQUESTS_CA_BUNDLE", value="/proxy-certs/ca-bundle.crt"))
        main.env = main_env

        mounts = list(main.volume_mounts or [])
        mounts.append(k8s_client.V1VolumeMount(name="proxy-certs", mount_path="/proxy-certs", read_only=True))
        mounts.append(k8s_client.V1VolumeMount(name="proxy-data", mount_path="/proxy-data", read_only=True))
        main.volume_mounts = mounts

        # Drop all Linux capabilities except SETUID/SETGID which Harbor's
        # exec path needs (`su <user> -s /bin/bash -c ...` for exec_as_root
        # and exec_as_agent).  allowPrivilegeEscalation must remain True for
        # the same reason: the kernel requires it for setuid binaries like su.
        # runAsNonRoot and readOnlyRootFilesystem are intentionally omitted:
        # SWE-bench images build as root (no USER directive), verifiers write to
        # /etc and /root at runtime, and conda envs live in root-owned paths.
        main.security_context = k8s_client.V1SecurityContext(
            capabilities=k8s_client.V1Capabilities(
                drop=["ALL"],
                add=["SETUID", "SETGID"],
            ),
        )

        containers.append(self._proxy_container())
        return containers

    def _build_pod_spec(self) -> k8s_client.V1PodSpec:
        spec = super()._build_pod_spec()

        # Prevent untrusted agent code from querying or mutating the K8s API
        # via the auto-mounted ServiceAccount token.
        spec.automount_service_account_token = False

        # iptables init container: redirect all outbound TCP 443 traffic to the
        # proxy sidecar (localhost:15443), except traffic from UID 1337 (the proxy
        # itself).  This replaces the old hostAliases + socket.getaddrinfo patch
        # approach with the standard Istio-style transparent interception pattern.
        # Private CIDRs are excluded so in-cluster services are not intercepted.
        spec.init_containers = [
            k8s_client.V1Container(
                name="iptables-init",
                image="alpine:3.20",
                image_pull_policy="IfNotPresent",
                command=[
                    "sh",
                    "-c",
                    " && ".join(
                        [
                            "apk add --no-cache iptables",
                            "iptables -t nat -N PROXY_OUTPUT",
                            "iptables -t nat -A PROXY_OUTPUT -m owner --uid-owner 1337 -j RETURN",
                            "iptables -t nat -A PROXY_OUTPUT -d 127.0.0.0/8 -j RETURN",
                            "iptables -t nat -A PROXY_OUTPUT -d 10.0.0.0/8 -j RETURN",
                            "iptables -t nat -A PROXY_OUTPUT -d 172.16.0.0/12 -j RETURN",
                            "iptables -t nat -A PROXY_OUTPUT -d 192.168.0.0/16 -j RETURN",
                            "iptables -t nat -A PROXY_OUTPUT -p tcp --dport 443 -j REDIRECT --to-ports 15443",
                            "iptables -t nat -A OUTPUT -p tcp -j PROXY_OUTPUT",
                        ]
                    ),
                ],
                security_context=k8s_client.V1SecurityContext(
                    capabilities=k8s_client.V1Capabilities(add=["NET_ADMIN"]),
                    run_as_user=0,
                ),
            )
        ]
        return spec

    def _proxy_container(self) -> k8s_client.V1Container:
        env = [
            k8s_client.V1EnvVar(name="MAX_COST_USD", value=self.max_cost_usd),
            k8s_client.V1EnvVar(name="EVALUATION_RUN_ID", value=self.evaluation_run_id),
        ]
        for k, v in self.openrouter_sidecar_env.items():
            # sidecar_env_vars() returns RIDGES_-prefixed names for Docker
            # Compose compat; the proxy reads the unprefixed names directly.
            name = k.removeprefix("RIDGES_")
            env.append(k8s_client.V1EnvVar(name=name, value=v))

        return k8s_client.V1Container(
            name="proxy",
            image=self.proxy_image,
            # IfNotPresent avoids the `:latest`-tag default of Always, so a
            # kind-loaded image (or a cached node image) is used without re-pull.
            image_pull_policy="IfNotPresent",
            env=env,
            # UID 1337 matches the proxy user in the Dockerfile.  The iptables
            # init container exempts UID 1337 from the port-443 REDIRECT rule,
            # so the proxy's own upstream connections reach the internet directly
            # while all other containers' port-443 traffic is transparently
            # intercepted.
            security_context=k8s_client.V1SecurityContext(
                run_as_user=1337,
                allow_privilege_escalation=False,
                capabilities=k8s_client.V1Capabilities(drop=["ALL"]),
            ),
            volume_mounts=[
                # Mount at /certs/output, not /certs, so the emptyDir does not
                # shadow the certs baked into the image at /certs/{ca,server}.*
                # The entrypoint writes ca-bundle.crt and ridges-ca.crt here.
                k8s_client.V1VolumeMount(name="proxy-certs", mount_path="/certs/output"),
                k8s_client.V1VolumeMount(name="proxy-data", mount_path="/proxy-data"),
            ],
            ports=[k8s_client.V1ContainerPort(container_port=15443)],
        )

    # ------------------------------------------------------------------
    # On-demand image building via Kaniko
    # ------------------------------------------------------------------

    async def _ensure_image(self, *, force_build: bool = False) -> None:
        """Check registry for the task image; build with Kaniko if missing."""
        image_ref = f"{self.registry}/{self.task_name}:{self.digest_tag}"

        if not force_build and await self._image_exists_in_registry(image_ref):
            self.logger.debug(f"Image {image_ref} already in registry – skipping build")
            return

        job_name = f"build-{self._slug(self.task_name)}-{self.digest_tag}"
        secret_name = f"{job_name}-url"
        self.logger.info(f"Building image {image_ref} via Kaniko job {job_name}")

        try:
            await asyncio.to_thread(self._create_build_secret_sync, secret_name)
            await asyncio.to_thread(self._create_kaniko_job_sync, job_name, secret_name, image_ref)
        except ApiException as exc:
            if exc.status == 409:
                if await self._is_job_failed(job_name):
                    self.logger.warning(f"Kaniko job {job_name} previously failed — deleting and retrying")
                    await self._delete_job(job_name)
                    await self._delete_secret(secret_name)
                    await asyncio.to_thread(self._create_build_secret_sync, secret_name)
                    await asyncio.to_thread(self._create_kaniko_job_sync, job_name, secret_name, image_ref)
                else:
                    self.logger.debug(f"Kaniko job {job_name} already exists — another screener is building")
            else:
                raise

        await self._wait_for_build_job(job_name, secret_name, timeout_sec=600)

    async def _image_exists_in_registry(self, image_ref: str) -> bool:
        """HEAD-check the task image (self.task_name:self.digest_tag) in the registry."""
        import base64
        import http.client
        import ssl
        import urllib.parse

        name, tag = self.task_name, self.digest_tag
        try:
            scheme = "http" if self._registry_insecure else "https"
            default_port = 5000 if self._registry_insecure else 443
            parsed = urllib.parse.urlsplit(f"{scheme}://{self.registry}")
            host = parsed.hostname or self.registry.split(":")[0]
            port = parsed.port or default_port

            def _head() -> bool:
                if self._registry_insecure:
                    conn: http.client.HTTPConnection = http.client.HTTPConnection(host, port, timeout=10)
                else:
                    ctx = ssl.create_default_context()
                    conn = http.client.HTTPSConnection(host, port, timeout=10, context=ctx)
                headers: dict[str, str] = {}
                if self.registry_credentials_secret and self._registry_password:
                    cred = base64.b64encode(b"kaniko:" + self._registry_password.encode()).decode()
                    headers["Authorization"] = f"Basic {cred}"
                conn.request("HEAD", f"/v2/{name}/manifests/{tag}", headers=headers)
                resp = conn.getresponse()
                conn.close()
                return resp.status == 200

            return await asyncio.to_thread(_head)
        except Exception as exc:
            self.logger.debug(f"Registry check failed for {name}:{tag}: {exc}")
            return False

    def _create_build_secret_sync(self, secret_name: str) -> None:
        """Create a Secret holding the presigned URL for the Kaniko init container."""
        secret = k8s_client.V1Secret(
            metadata=k8s_client.V1ObjectMeta(
                name=secret_name,
                namespace=self.namespace,
                labels={"ridges.ai/managed-by": "screener", "ridges.ai/build-url": "true"},
            ),
            string_data={"url": self.task_archive_presigned_url},
        )
        self._api.create_namespaced_secret(namespace=self.namespace, body=secret)

    def _create_kaniko_job_sync(self, job_name: str, secret_name: str, image_ref: str) -> None:
        """Create the Kaniko build Job."""
        init_container = k8s_client.V1Container(
            name="fetch-context",
            image="curlimages/curl:latest",
            command=["/bin/sh", "-c"],
            args=[
                'curl -sSfL "$PRESIGNED_URL" -o /tmp/task.tar.gz && '
                "mkdir -p /workspace && "
                "tar -xzf /tmp/task.tar.gz -C /workspace --strip-components=1 && "
                "test -f /workspace/environment/Dockerfile"
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
            "--context=dir:///workspace/environment",
            f"--destination={image_ref}",
            "--cache=true",
            f"--cache-repo={self.registry}/cache",
            f"--registry-mirror={self.registry}",
        ]
        if self._registry_insecure:
            kaniko_args.append(f"--insecure-registry={self.registry}")

        kaniko_volume_mounts = [
            k8s_client.V1VolumeMount(name="context", mount_path="/workspace"),
        ]
        if self.registry_credentials_secret:
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
        if self.registry_credentials_secret:
            volumes.append(
                k8s_client.V1Volume(
                    name="docker-config",
                    secret=k8s_client.V1SecretVolumeSource(
                        secret_name=self.registry_credentials_secret,
                        items=[
                            k8s_client.V1KeyToPath(
                                key=".dockerconfigjson",
                                path="config.json",
                            )
                        ],
                    ),
                )
            )

        job = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={"ridges.ai/managed-by": "screener", "ridges.ai/build-job": "true"},
            ),
            spec=k8s_client.V1JobSpec(
                backoff_limit=2,
                ttl_seconds_after_finished=300,
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(
                        labels={"ridges.ai/managed-by": "screener", "ridges.ai/build-job": "true"},
                    ),
                    spec=k8s_client.V1PodSpec(
                        init_containers=[init_container],
                        containers=[kaniko_container],
                        restart_policy="Never",
                        volumes=volumes,
                    ),
                ),
            ),
        )
        self._batch.create_namespaced_job(namespace=self.namespace, body=job)

    async def _wait_for_build_job(self, job_name: str, secret_name: str, timeout_sec: int = 600) -> None:
        """Poll until the Kaniko Job succeeds or fails."""
        self.logger.debug(f"Waiting for Kaniko build job {job_name} (timeout={timeout_sec}s)")
        deadline = asyncio.get_event_loop().time() + timeout_sec

        while asyncio.get_event_loop().time() < deadline:
            try:
                job = await asyncio.to_thread(
                    self._batch.read_namespaced_job,
                    name=job_name,
                    namespace=self.namespace,
                )
            except ApiException as exc:
                if exc.status == 404:
                    await asyncio.sleep(5)
                    continue
                raise

            conditions = job.status.conditions or []
            for cond in conditions:
                if cond.type == "Complete" and cond.status == "True":
                    self.logger.info(f"Kaniko build job {job_name} completed successfully")
                    # Clean up the secret (best-effort)
                    await self._delete_secret(secret_name)
                    return
                if cond.type == "Failed" and cond.status == "True":
                    await self._delete_secret(secret_name)
                    raise RuntimeError(f"Kaniko build job {job_name} failed: {cond.message}")

            await asyncio.sleep(5)

        await self._delete_secret(secret_name)
        raise RuntimeError(f"Kaniko build job {job_name} did not complete within {timeout_sec}s")

    async def _is_job_failed(self, job_name: str) -> bool:
        """Return True if the named Job exists and is in Failed state."""
        try:
            job = await asyncio.to_thread(
                self._batch.read_namespaced_job,
                name=job_name,
                namespace=self.namespace,
            )
            for cond in job.status.conditions or []:
                if cond.type == "Failed" and cond.status == "True":
                    return True
            return False
        except ApiException:
            return False

    async def _delete_job(self, job_name: str) -> None:
        """Delete a Kaniko Job (background propagation so pods are also removed)."""
        try:
            await asyncio.to_thread(
                self._batch.delete_namespaced_job,
                name=job_name,
                namespace=self.namespace,
                body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
            )
            await asyncio.sleep(2)
        except ApiException as exc:
            if exc.status != 404:
                self.logger.warning(f"Failed to delete job {job_name}: {exc}")

    async def _delete_secret(self, secret_name: str) -> None:
        try:
            await asyncio.to_thread(
                self._api.delete_namespaced_secret,
                name=secret_name,
                namespace=self.namespace,
            )
        except ApiException as exc:
            if exc.status != 404:
                self.logger.warning(f"Failed to delete build Secret {secret_name}: {exc}")

    @staticmethod
    def _slug(name: str) -> str:
        """Sanitise a task name for use in Kubernetes resource names."""
        slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
        return slug[:40].strip("-")
