import io
import logging
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

from ridges_harbor.k8s_environment import (
    AGENT_IMAGE_ROLE,
    VERIFIER_IMAGE_ROLE,
    KubernetesEnvironment,
    RidgesKubernetesEnvironment,
    _build_job_body,
    _build_job_name,
    _environment_image_role,
    pre_build_images,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _write_pg_compose(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "docker-compose.yaml").write_text(
        "services:\n"
        "  postgres:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: postgres.Dockerfile\n"
        "    volumes:\n"
        "      - type: tmpfs\n"
        "        target: /var/lib/postgresql/data\n"
        "        tmpfs:\n"
        "          size: 4294967296\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: secret\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "pg_isready"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 300\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        "          memory: 6g\n"
        "        reservations:\n"
        "          memory: 5g\n"
        "          cpus: '0.5'\n"
        "  redis:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: redis.Dockerfile\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "redis-cli ping"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 60\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        "          memory: 1g\n"
        "        reservations:\n"
        "          memory: 512m\n"
        "          cpus: '0.1'\n"
    )


def _write_clickhouse_compose(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "docker-compose.yaml").write_text(
        "services:\n"
        "  clickhouse:\n"
        "    image: clickhouse/clickhouse-server@sha256:35b419db86eed71ab1c41c03b4fd1f39be26f41eb38b0866268e2ca162445105\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 60\n"
        "    volumes:\n"
        "      - type: tmpfs\n"
        "        target: /var/lib/clickhouse\n"
        "        tmpfs:\n"
        "          size: 1073741824\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        "          memory: 4g\n"
        "        reservations:\n"
        "          memory: 2g\n"
        "          cpus: '0.5'\n"
    )


def _make_environment(
    tmp_path: Path,
    *,
    session_id: str,
    verifier_image_required: bool = False,
) -> RidgesKubernetesEnvironment:
    return RidgesKubernetesEnvironment(
        environment_dir=tmp_path / ("environment" if session_id.endswith("__env") else "tests"),
        environment_name="native-separate-task",
        session_id=session_id,
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(
            cpus=1,
            memory_mb=1024,
            storage_mb=1024,
            workdir="/app",
        ),
        registry="registry.example.test",
        task_name="native-separate-task",
        digest_tag="abc123def456",
        task_archive_presigned_url="https://tasks.example.test/task.tar.gz",
        proxy_image="sandbox-proxy:test",
        evaluation_run_id="evaluation-1",
        proxy_data_dir=tmp_path / "proxy-data",
        verifier_image_required=verifier_image_required,
        task_dir=tmp_path,
        build_registry="build-registry.example.test:5000",
    )


def test_environment_image_role_classifies_agent_suffix_before_verifier_text() -> None:
    assert _environment_image_role("task__verifier__in-name__env") == AGENT_IMAGE_ROLE
    assert _environment_image_role("task__verifier__trial") == VERIFIER_IMAGE_ROLE

    with pytest.raises(ValueError, match="Unsupported Harbor environment session"):
        _environment_image_role("unknown-session")


def test_kubernetes_requires_explicit_task_resources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cpus, memory_mb, storage_mb"):
        RidgesKubernetesEnvironment(
            environment_dir=tmp_path / "environment",
            environment_name="missing-resources",
            session_id="missing-resources__env",
            trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
            task_env_config=EnvironmentConfig(workdir="/app"),
            registry="registry.example.test",
            task_name="missing-resources",
            digest_tag="abc123def456",
            task_archive_presigned_url="https://tasks.example.test/task.tar.gz",
            proxy_image="sandbox-proxy:test",
            evaluation_run_id="evaluation-1",
        )


def test_agent_and_verifier_use_distinct_images_and_pod_shapes(tmp_path: Path) -> None:
    agent = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    verifier = _make_environment(
        tmp_path,
        session_id="native-separate-task__verifier__trial",
    )

    assert agent.image.endswith(":abc123def456-agent")
    assert verifier.image.endswith(":abc123def456-verifier")
    assert agent.proxy_data_dir == tmp_path / "proxy-data"
    assert verifier.proxy_data_dir is None
    assert [container.name for container in agent._build_containers()] == ["main", "proxy"]
    assert [container.name for container in verifier._build_containers()] == ["main"]
    assert agent._build_labels()["ridges.ai/phase"] == "agent"
    assert verifier._build_labels()["ridges.ai/phase"] == "verification"
    assert agent._build_pod_spec().init_containers is not None
    assert verifier._build_pod_spec().init_containers is None


def test_agent_and_verifier_pods_include_compose_sidecars(tmp_path: Path) -> None:
    _write_pg_compose(tmp_path / "environment")
    _write_pg_compose(tmp_path / "tests")
    agent = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    verifier = _make_environment(
        tmp_path,
        session_id="native-separate-task__verifier__trial",
    )

    assert [container.name for container in agent._build_containers()] == [
        "main",
        "proxy",
        "postgres",
        "redis",
    ]
    assert [container.name for container in verifier._build_containers()] == ["main", "postgres", "redis"]
    postgres = next(container for container in agent._build_containers() if container.name == "postgres")
    assert postgres.image.endswith(":abc123def456-agent-postgres")
    assert postgres.startup_probe.failure_threshold == 300
    assert postgres.startup_probe._exec.command == ["sh", "-c", "pg_isready"]
    assert postgres.security_context is None
    tmpfs_name = agent._tmpfs_volume_name("postgres", 0)
    tmpfs = next(volume for volume in agent._build_volumes() if volume.name == tmpfs_name)
    assert tmpfs.empty_dir.medium == "Memory"
    assert tmpfs.empty_dir.size_limit == "4294967296"
    assert postgres.resources.requests["cpu"] == "500m"
    assert postgres.resources.requests["memory"] == "5Gi"
    assert postgres.resources.limits["memory"] == "6Gi"
    assert "ephemeral-storage" not in postgres.resources.requests
    assert "ephemeral-storage" not in postgres.resources.limits
    redis = next(container for container in agent._build_containers() if container.name == "redis")
    assert "ephemeral-storage" not in redis.resources.requests
    assert redis.resources.requests["memory"] == "512Mi"
    aliases = agent._build_pod_spec().host_aliases
    assert aliases is not None
    assert aliases[0].ip == "127.0.0.1"
    assert aliases[0].hostnames == ["postgres", "redis"]
    assert verifier._build_pod_spec().init_containers is None
    assert agent._pod_ready_timeout_sec() == 1200


def test_tmpfs_volume_names_are_short_stable_and_collision_safe(tmp_path: Path) -> None:
    environment = _make_environment(tmp_path, session_id="native-separate-task__env")
    first = environment._tmpfs_volume_name("a" * 63, 0)
    repeated = environment._tmpfs_volume_name("a" * 63, 0)
    second = environment._tmpfs_volume_name("a" * 63, 1)

    assert first == repeated
    assert first != second
    assert len(first) <= 63


def test_generated_container_names_must_be_unique() -> None:
    containers = [SimpleNamespace(name="main"), SimpleNamespace(name="main")]

    with pytest.raises(RuntimeError, match="duplicate Kubernetes container names"):
        RidgesKubernetesEnvironment._validate_container_names(containers)


@pytest.mark.anyio
async def test_wait_for_pod_ready_fails_on_terminated_container_while_running() -> None:
    environment = KubernetesEnvironment.__new__(KubernetesEnvironment)
    environment.pod_name = "pod-test"
    environment.namespace = "default"
    environment.logger = logging.getLogger(__name__)
    environment._core_api = SimpleNamespace(
        read_namespaced_pod=lambda **_kwargs: SimpleNamespace(
            status=SimpleNamespace(
                phase="Running",
                reason=None,
                message=None,
                container_statuses=[
                    SimpleNamespace(
                        name="postgres",
                        ready=False,
                        state=SimpleNamespace(
                            waiting=None,
                            terminated=SimpleNamespace(exit_code=1),
                        ),
                    )
                ],
            )
        )
    )

    with pytest.raises(RuntimeError, match="terminated required container"):
        await environment._wait_for_pod_ready(timeout_sec=1)


def test_clickhouse_sidecar_uses_registry_tag_not_hub_ref(tmp_path: Path) -> None:
    _write_clickhouse_compose(tmp_path / "environment")
    agent = _make_environment(tmp_path, session_id="native-separate-task__env")
    clickhouse = next(container for container in agent._build_containers() if container.name == "clickhouse")
    assert clickhouse.image.endswith(":abc123def456-agent-clickhouse")
    assert "clickhouse/clickhouse-server@" not in clickhouse.image


@pytest.mark.anyio
async def test_agent_start_builds_both_images_before_pod(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    events: list[tuple[object, ...]] = []

    async def ensure_client() -> None:
        events.append(("client",))

    async def ensure_image(*, image_role: str, force_build: bool, allow_build: bool) -> None:
        events.append(("image", image_role, force_build, allow_build))

    async def start_pod(self, force_build: bool) -> None:
        events.append(("pod", force_build))

    monkeypatch.setattr(environment, "_ensure_client", ensure_client)
    monkeypatch.setattr(environment, "_ensure_image", ensure_image)
    monkeypatch.setattr(KubernetesEnvironment, "start", start_pod)

    await environment.start(force_build=True)

    assert events[0] == ("client",)
    assert events[-1] == ("pod", False)
    assert sorted(events[1:-1]) == [
        ("image", AGENT_IMAGE_ROLE, True, True),
        ("image", VERIFIER_IMAGE_ROLE, True, True),
    ]


@pytest.mark.anyio
async def test_agent_start_gathers_sidecar_images(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_pg_compose(tmp_path / "environment")
    _write_clickhouse_compose(tmp_path / "tests")
    environment = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    events: list[tuple[object, ...]] = []

    async def ensure_client() -> None:
        events.append(("client",))

    async def ensure_image(
        *,
        image_role: str,
        force_build: bool,
        allow_build: bool,
        dockerfile_name: str = "Dockerfile",
        from_image: str | None = None,
    ) -> None:
        events.append(("image", image_role, force_build, allow_build, dockerfile_name, from_image))

    async def start_pod(self, force_build: bool) -> None:
        events.append(("pod", force_build))

    monkeypatch.setattr(environment, "_ensure_client", ensure_client)
    monkeypatch.setattr(environment, "_ensure_image", ensure_image)
    monkeypatch.setattr(KubernetesEnvironment, "start", start_pod)

    await environment.start(force_build=True)

    assert events[0] == ("client",)
    assert events[-1] == ("pod", False)
    assert sorted(events[1:-1]) == [
        ("image", AGENT_IMAGE_ROLE, True, True, "Dockerfile", None),
        ("image", "agent-postgres", True, True, "postgres.Dockerfile", None),
        ("image", "agent-redis", True, True, "redis.Dockerfile", None),
        ("image", VERIFIER_IMAGE_ROLE, True, True, "Dockerfile", None),
        (
            "image",
            "verifier-clickhouse",
            True,
            True,
            "ridges-from-clickhouse.Dockerfile",
            "clickhouse/clickhouse-server@sha256:35b419db86eed71ab1c41c03b4fd1f39be26f41eb38b0866268e2ca162445105",
        ),
    ]


@pytest.mark.anyio
async def test_agent_stop_requests_deletion_without_waiting_for_pod_absence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    events: list[tuple[object, ...]] = []

    class CoreApi:
        def delete_namespaced_pod(self, **kwargs: Any) -> None:
            events.append(("delete", kwargs["name"], kwargs["namespace"]))

        def read_namespaced_pod(self, **_kwargs: Any) -> None:
            raise AssertionError("normal teardown must not poll for Pod absence")

    async def download_dir(remote_path: str, local_path: Path) -> None:
        events.append(("download", remote_path, local_path))

    environment._core_api = CoreApi()  # type: ignore[assignment]
    monkeypatch.setattr(environment, "download_dir", download_dir)

    await environment.stop()

    assert events == [
        ("download", "/proxy-data", tmp_path / "proxy-data"),
        ("delete", environment.pod_name, environment.namespace),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("session_id", "verifier_image_required"),
    [
        ("shared-task__env", False),
        ("native-separate-task__verifier__trial", False),
    ],
)
async def test_other_pod_teardown_still_waits_for_absence(
    tmp_path: Path,
    monkeypatch,
    session_id: str,
    verifier_image_required: bool,
) -> None:
    environment = _make_environment(
        tmp_path,
        session_id=session_id,
        verifier_image_required=verifier_image_required,
    )
    waits: list[bool] = []

    async def download_dir(_remote_path: str, _local_path: Path) -> None:
        return None

    async def delete_pod_and_wait(
        _timeout_sec: int = 60,
        *,
        wait_for_absence: bool = True,
    ) -> None:
        waits.append(wait_for_absence)

    environment._core_api = object()  # type: ignore[assignment]
    monkeypatch.setattr(environment, "download_dir", download_dir)
    monkeypatch.setattr(environment, "_delete_pod_and_wait", delete_pod_and_wait)

    await environment.stop()

    assert waits == [True]


@pytest.mark.anyio
async def test_verifier_start_requires_prebuilt_image_and_never_builds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment = _make_environment(
        tmp_path,
        session_id="native-separate-task__verifier__trial",
    )

    async def image_missing(_image_tag: str) -> bool:
        return False

    monkeypatch.setattr(environment, "_image_exists_in_registry", image_missing)

    with pytest.raises(RuntimeError, match="Required prebuilt verifier image is missing"):
        await environment._ensure_image(
            image_role=VERIFIER_IMAGE_ROLE,
            force_build=False,
            allow_build=False,
        )


def test_declared_mem_limit_reaches_sidecar_resources(tmp_path: Path) -> None:
    (tmp_path / "environment").mkdir()
    (tmp_path / "environment" / "docker-compose.yaml").write_text(
        "services:\n  redis:\n"
        "    image: redis@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        '    healthcheck:\n      test: ["CMD-SHELL", "redis-cli ping"]\n'
        "    mem_limit: 1g\n"
    )
    agent = _make_environment(tmp_path, session_id="native-separate-task__env")
    redis = next(container for container in agent._build_containers() if container.name == "redis")
    assert redis.resources.requests["memory"] == "1Gi"
    assert redis.resources.limits["memory"] == "1Gi"


@pytest.mark.anyio
async def test_agent_start_requires_task_dir_for_verifier_sidecars(tmp_path: Path, monkeypatch) -> None:
    environment = _make_environment(
        tmp_path,
        session_id="native-separate-task__env",
        verifier_image_required=True,
    )
    environment.task_dir = None

    async def ensure_client() -> None:
        return None

    monkeypatch.setattr(environment, "_ensure_client", ensure_client)

    with pytest.raises(RuntimeError, match="task_dir is required"):
        await environment.start(force_build=True)


def test_agent_build_job_uses_environment_context() -> None:
    job = _build_job_body(
        "build-task-agent",
        "build-task-agent-url",
        "registry.example.test/task:abc-agent",
        0,
        "default",
        "registry.example.test",
        False,
        None,
        context_name="environment",
    )

    init = job.spec.template.spec.init_containers[0]
    init_env = {env.name: env.value for env in init.env if env.value is not None}
    build_args = job.spec.template.spec.containers[0].args
    assert init_env["CONTEXT_NAME"] == "environment"
    assert init_env["DOCKERFILE_NAME"] == "Dockerfile"
    assert "$CONTEXT_NAME" in init.args[0]
    assert "$DOCKERFILE_NAME" in init.args[0]
    assert "--local=context=/workspace/environment" in build_args
    assert "--local=dockerfile=/workspace/environment" in build_args
    assert "--opt=filename=Dockerfile" in build_args


def test_sidecar_build_job_uses_named_dockerfile() -> None:
    job = _build_job_body(
        "build-task-agent-postgres",
        "build-task-agent-postgres-url",
        "registry.example.test/task:abc-agent-postgres",
        0,
        "default",
        "registry.example.test",
        False,
        None,
        context_name="environment",
        dockerfile_name="postgres.Dockerfile",
    )

    init = job.spec.template.spec.init_containers[0]
    init_env = {env.name: env.value for env in init.env if env.value is not None}
    build_args = job.spec.template.spec.containers[0].args
    assert init_env["CONTEXT_NAME"] == "environment"
    assert init_env["DOCKERFILE_NAME"] == "postgres.Dockerfile"
    assert "$DOCKERFILE_NAME" in init.args[0]
    assert "--opt=filename=postgres.Dockerfile" in build_args


def test_from_copy_build_job_writes_generated_dockerfile() -> None:
    job = _build_job_body(
        "build-task-agent-clickhouse",
        "build-task-agent-clickhouse-url",
        "registry.example.test/task:abc-agent-clickhouse",
        0,
        "default",
        "registry.example.test",
        False,
        None,
        context_name="environment",
        dockerfile_name="ridges-from-clickhouse.Dockerfile",
        from_image="clickhouse/clickhouse-server@sha256:abc",
    )

    init = job.spec.template.spec.init_containers[0]
    init_env = {env.name: env.value for env in init.env if env.value is not None}
    assert init_env["FROM_IMAGE"].endswith("@sha256:abc")
    assert init_env["DOCKERFILE_NAME"] == "ridges-from-clickhouse.Dockerfile"
    assert "$DOCKERFILE_NAME" in init.args[0]
    assert "--opt=filename=ridges-from-clickhouse.Dockerfile" in job.spec.template.spec.containers[0].args


def test_verifier_build_job_uses_tests_context() -> None:
    job = _build_job_body(
        "build-task-verifier",
        "build-task-verifier-url",
        "registry.example.test/task:abc-verifier",
        0,
        "default",
        "registry.example.test",
        False,
        None,
        context_name="tests",
    )

    init = job.spec.template.spec.init_containers[0]
    init_env = {env.name: env.value for env in init.env if env.value is not None}
    build_args = job.spec.template.spec.containers[0].args
    assert init_env["CONTEXT_NAME"] == "tests"
    assert init_env["DOCKERFILE_NAME"] == "Dockerfile"
    assert "--local=context=/workspace/tests" in build_args
    assert "--local=dockerfile=/workspace/tests" in build_args
    assert "--opt=filename=Dockerfile" in build_args


@pytest.mark.parametrize("image_role", [AGENT_IMAGE_ROLE, VERIFIER_IMAGE_ROLE])
def test_build_job_names_fit_kubernetes_dns_label_limit(image_role: str) -> None:
    name = _build_job_name("Task-" + "very-long-name-" * 8, "abc123def456", image_role)

    assert len(name) <= 59
    assert len(f"{name}-url") <= 63
    assert name.endswith(f"-abc123def456-{image_role}")


def test_truncated_build_job_names_retain_full_task_identity() -> None:
    shared_prefix = "task-" + "same-prefix-" * 8

    first = _build_job_name(shared_prefix + "one", "abc123def456", AGENT_IMAGE_ROLE)
    second = _build_job_name(shared_prefix + "two", "abc123def456", AGENT_IMAGE_ROLE)

    assert first != second


def _tar_gz_with_compose(
    *,
    environment: str | None = None,
    tests: str | None = None,
    tests_dockerfile: str | None = None,
    prefix: str = "task",
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:

        def add(path: str, text: str) -> None:
            data = text.encode()
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        if environment is not None:
            add(
                f"{prefix}/environment/docker-compose.yaml" if prefix else "environment/docker-compose.yaml",
                environment,
            )
        if tests is not None:
            add(f"{prefix}/tests/docker-compose.yaml" if prefix else "tests/docker-compose.yaml", tests)
        if tests_dockerfile is not None:
            add(f"{prefix}/tests/Dockerfile" if prefix else "tests/Dockerfile", tests_dockerfile)
    return buffer.getvalue()


@pytest.mark.anyio
async def test_pre_build_peeks_compose_when_agent_tag_exists(monkeypatch) -> None:
    from ridges_harbor import k8s_environment as k8s_environment_module

    jobs: list[str] = []
    existing = {"abc123def456-agent"}

    class CoreApi:
        def create_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

        def delete_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

    class BatchApi:
        def create_namespaced_job(self, **kwargs: Any) -> None:
            jobs.append(kwargs["body"].metadata.name)

    async def registry_exists(_registry: str, _task_name: str, tag: str, **_kwargs: Any) -> bool:
        return tag in existing

    monkeypatch.setattr(k8s_environment_module, "_init_standalone_k8s_clients", lambda _ctx: (CoreApi(), BatchApi()))
    monkeypatch.setattr(k8s_environment_module, "_registry_image_exists", registry_exists)
    monkeypatch.setattr(
        k8s_environment_module,
        "_download_task_archive",
        lambda _url: _tar_gz_with_compose(
            environment=(
                "services:\n  postgres:\n    build:\n      context: .\n"
                "      dockerfile: postgres.Dockerfile\n"
                '    healthcheck:\n      test: ["CMD-SHELL", "pg_isready"]\n'
            ),
            tests=(
                "services:\n  clickhouse:\n"
                "    image: clickhouse/clickhouse-server@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                '    healthcheck:\n      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping"]\n'
            ),
        ),
    )

    await pre_build_images(
        [("native-task", "abc123def456", "https://tasks.example.test/task.tar.gz")],
        namespace="default",
        registry="registry.example.test",
        build_registry="build-registry.example.test:5000",
    )

    assert any(name.endswith("-abc123def456-agent-postgres") for name in jobs)
    assert any(name.endswith("-abc123def456-verifier-clickhouse") for name in jobs)
    assert not any(name.endswith("-abc123def456-agent") and "postgres" not in name for name in jobs)
    assert not any(name.endswith("-abc123def456-verifier") and "clickhouse" not in name for name in jobs)


@pytest.mark.anyio
async def test_pre_build_skips_existing_sidecar_tags(monkeypatch) -> None:
    from ridges_harbor import k8s_environment as k8s_environment_module

    jobs: list[str] = []
    existing = {"abc123def456-agent", "abc123def456-agent-postgres"}

    class CoreApi:
        def create_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

        def delete_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

    class BatchApi:
        def create_namespaced_job(self, **kwargs: Any) -> None:
            jobs.append(kwargs["body"].metadata.name)

    async def registry_exists(_registry: str, _task_name: str, tag: str, **_kwargs: Any) -> bool:
        return tag in existing

    monkeypatch.setattr(k8s_environment_module, "_init_standalone_k8s_clients", lambda _ctx: (CoreApi(), BatchApi()))
    monkeypatch.setattr(k8s_environment_module, "_registry_image_exists", registry_exists)
    monkeypatch.setattr(
        k8s_environment_module,
        "_download_task_archive",
        lambda _url: _tar_gz_with_compose(
            environment=(
                "services:\n  postgres:\n    build:\n      context: .\n"
                "      dockerfile: postgres.Dockerfile\n"
                '    healthcheck:\n      test: ["CMD-SHELL", "pg_isready"]\n'
            ),
        ),
    )

    await pre_build_images(
        [("native-task", "abc123def456", "https://tasks.example.test/task.tar.gz")],
        namespace="default",
        registry="registry.example.test",
        build_registry="build-registry.example.test:5000",
    )

    assert jobs == []


def _install_pre_build_mocks(monkeypatch, *, archive: bytes, existing: set[str]) -> list[str]:
    from ridges_harbor import k8s_environment as k8s_environment_module

    jobs: list[str] = []

    class CoreApi:
        def create_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

        def delete_namespaced_secret(self, **_kwargs: Any) -> None:
            return None

    class BatchApi:
        def create_namespaced_job(self, **kwargs: Any) -> None:
            jobs.append(kwargs["body"].metadata.name)

    async def registry_exists(_registry: str, _task_name: str, tag: str, **_kwargs: Any) -> bool:
        return tag in existing

    monkeypatch.setattr(k8s_environment_module, "_init_standalone_k8s_clients", lambda _ctx: (CoreApi(), BatchApi()))
    monkeypatch.setattr(k8s_environment_module, "_registry_image_exists", registry_exists)
    monkeypatch.setattr(k8s_environment_module, "_download_task_archive", lambda _url: archive)
    return jobs


@pytest.mark.anyio
async def test_pre_build_queues_verifier_main_when_tests_dockerfile_present(monkeypatch) -> None:
    jobs = _install_pre_build_mocks(
        monkeypatch,
        archive=_tar_gz_with_compose(
            environment="services:\n  main: {}\n",
            tests_dockerfile="FROM alpine:3.20\nCOPY . /tests\n",
        ),
        existing={"abc123def456-agent"},
    )

    await pre_build_images(
        [("native-task", "abc123def456", "https://tasks.example.test/task.tar.gz")],
        namespace="default",
        registry="registry.example.test",
        build_registry="build-registry.example.test:5000",
    )

    assert any(name.endswith("-abc123def456-verifier") and "clickhouse" not in name for name in jobs)


@pytest.mark.anyio
async def test_pre_build_skips_existing_verifier_main(monkeypatch) -> None:
    jobs = _install_pre_build_mocks(
        monkeypatch,
        archive=_tar_gz_with_compose(
            environment="services:\n  main: {}\n",
            tests_dockerfile="FROM alpine:3.20\nCOPY . /tests\n",
        ),
        existing={"abc123def456-agent", "abc123def456-verifier"},
    )

    await pre_build_images(
        [("native-task", "abc123def456", "https://tasks.example.test/task.tar.gz")],
        namespace="default",
        registry="registry.example.test",
        build_registry="build-registry.example.test:5000",
    )

    assert not any(name.endswith("-abc123def456-verifier") for name in jobs)


@pytest.mark.anyio
async def test_pre_build_does_not_queue_verifier_main_from_tests_compose_alone(monkeypatch) -> None:
    jobs = _install_pre_build_mocks(
        monkeypatch,
        archive=_tar_gz_with_compose(
            tests=(
                "services:\n  clickhouse:\n"
                "    image: clickhouse/clickhouse-server@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                '    healthcheck:\n      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8123/ping"]\n'
            ),
        ),
        existing={"abc123def456-agent"},
    )

    await pre_build_images(
        [("native-task", "abc123def456", "https://tasks.example.test/task.tar.gz")],
        namespace="default",
        registry="registry.example.test",
        build_registry="build-registry.example.test:5000",
    )

    assert any(name.endswith("-abc123def456-verifier-clickhouse") for name in jobs)
    assert not any(name.endswith("-abc123def456-verifier") and "clickhouse" not in name for name in jobs)
