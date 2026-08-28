from pathlib import Path
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

    init_args = job.spec.template.spec.init_containers[0].args
    build_args = job.spec.template.spec.containers[0].args
    assert "/workspace/environment/Dockerfile" in init_args[0]
    assert "--local=context=/workspace/environment" in build_args
    assert "--local=dockerfile=/workspace/environment" in build_args


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

    init_args = job.spec.template.spec.init_containers[0].args
    build_args = job.spec.template.spec.containers[0].args
    assert "/workspace/tests/Dockerfile" in init_args[0]
    assert "--local=context=/workspace/tests" in build_args
    assert "--local=dockerfile=/workspace/tests" in build_args


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
