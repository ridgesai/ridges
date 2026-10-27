import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from harbor.environments.base import ExecResult
from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.factory import EnvironmentFactory
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.config import EnvironmentConfig as RuntimeEnvironmentConfig
from harbor.models.trial.paths import TrialPaths
from harbor.trial.errors import EnvironmentStartTimeoutError
from harbor.trial.single_step import SingleStepTrial

import ridges_harbor.registry_environment as registry_module
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.registry_environment import (
    RegistryDockerEnvironment,
    registry_environment_kwargs,
    registry_startup_timeouts,
)
from ridges_harbor.task_image_contract import image_labels, image_ref

DIGEST = "sha256:" + "a" * 64
REGISTRY = "validator-registry-dev.ridges.ai"
IMAGE_DIGEST = "sha256:" + "b" * 64


def test_identity_vector():
    assert image_ref(REGISTRY, DIGEST, "verifier", "postgres", "linux/amd64") == (
        f"{REGISTRY}/tasks/v1/{'a' * 64}/verifier/postgres:linux-amd64"
    )


def test_kwargs_carry_the_platform_registry(monkeypatch):
    monkeypatch.delenv("RIDGES_TASK_IMAGE_PULL_TIMEOUT_SEC", raising=False)
    assert registry_environment_kwargs(REGISTRY, DIGEST) == {
        "task_image_registry": REGISTRY,
        "task_digest": DIGEST,
        "image_pull_timeout_sec": 300,
    }
    with pytest.raises(ValueError):
        registry_environment_kwargs("https://" + REGISTRY, DIGEST)


@pytest.mark.parametrize("timeout", ["0", "1801", "not-a-number"])
def test_invalid_timeout_rejected(monkeypatch, timeout):
    monkeypatch.setenv("RIDGES_TASK_IMAGE_PULL_TIMEOUT_SEC", timeout)
    with pytest.raises(ValueError):
        registry_environment_kwargs(REGISTRY, DIGEST)


@pytest.fixture(autouse=True)
def no_token_provider(monkeypatch):
    """Pulls use the host Docker config unless a test registers a provider."""
    monkeypatch.setattr(registry_module, "_token_provider", None)


@pytest.fixture
def make_environment(tmp_path, monkeypatch):
    environments = []
    monkeypatch.setattr(DockerEnvironment, "_validate_daemon_mode", lambda self: None)
    for name in ("_validate_image_os", "ensure_dirs", "_upload_environment_dir_after_start", "prepare_logs_for_host"):
        monkeypatch.setattr(DockerEnvironment, name, AsyncMock())

    def make(role="agent", sidecar=False, **kwargs):
        kwargs.setdefault("native_startup_timeout_sec", 600)
        task_dir = tmp_path / f"task-{len(environments)}"
        context = task_dir / ("environment" if role == "agent" else "tests")
        context.mkdir(parents=True)
        (context / "Dockerfile").write_text("FROM scratch\n")
        compose = {"services": {"main": {"environment": {"UNCHANGED": "yes"}}, "redis": {"image": "redis:7"}}}
        if sidecar:
            (context / "Dockerfile.db").write_text("FROM postgres:16\n")
            compose["services"]["db"] = {
                "build": {"context": ".", "dockerfile": "Dockerfile.db"},
                "environment": {"DB": "test"},
            }
        (context / "docker-compose.yaml").write_text(yaml.safe_dump(compose))
        env = RegistryDockerEnvironment(
            environment_dir=context,
            environment_name="test",
            session_id=f"test__{role}",
            trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
            task_env_config=EnvironmentConfig(),
            task_image_registry=REGISTRY,
            task_digest=DIGEST,
            **kwargs,
        )
        env.commands = []

        async def compose_command(command, **kwargs):
            env.commands.append(command)
            return ExecResult(return_code=0)

        monkeypatch.setattr(env, "_run_docker_compose_command", compose_command)
        environments.append(env)
        return env

    yield make
    for env in environments:
        env._reset_registry_selection()
        env._cleanup_mounts_compose_file()
        env._cleanup_resources_compose_file()
        env._cleanup_env_compose_file()


@pytest.fixture
def start_in_harbor(monkeypatch):
    """Use Harbor's real agent/verifier startup deadlines without running an agent."""

    async def start(env, role, *, base_timeout, multiplier, force_build=False):
        trial = object.__new__(SingleStepTrial)
        trial.config = SimpleNamespace(
            environment=RuntimeEnvironmentConfig(
                force_build=force_build,
                kwargs={"native_startup_timeout_sec": env._native_startup_timeout_sec},
            ),
            environment_build_timeout_multiplier=multiplier,
            timeout_multiplier=1.0,
        )
        trial.task = SimpleNamespace(
            short_name="test", config=SimpleNamespace(environment=EnvironmentConfig(build_timeout_sec=base_timeout))
        )
        trial._environment_build_timeout_sec = trial._compute_environment_build_timeout_sec()
        trial.agent_environment = env
        if role == "agent":
            await trial._start_agent_environment()
        else:
            trial.paths = env.trial_paths
            trial.logger = env.logger
            trial._id = "registry-test"
            trial._verifier_env_build_context = lambda step: env.environment_dir
            trial._separate_verifier_session_id = lambda key: "test__verifier"
            trial._verifier_env_mounts = lambda config: []
            trial._validate_separate_verifier_env_policies = lambda env, **kwargs: None

            def create_verifier(**kwargs):
                assert kwargs["config"].kwargs["native_startup_timeout_sec"] == env._native_startup_timeout_sec
                return env

            monkeypatch.setattr(EnvironmentFactory, "create_environment_from_config", create_verifier)
            plan = SimpleNamespace(verifier_env_baseline=env.network_policy, verifier_phase=env.network_policy)
            async with trial._separate_verifier_env(env.task_env_config, key="test", plan=plan):
                pass

    return start


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["agent", "verifier"])
async def test_registry_timeout_preserves_native_budget_under_harbor(
    make_environment, start_in_harbor, monkeypatch, role
):
    base_timeout, pull_timeout = 0.1, 0.2
    native_timeout, multiplier = registry_startup_timeouts(base_timeout, 1.5, pull_timeout)
    env = make_environment(role=role, image_pull_timeout_sec=pull_timeout, native_startup_timeout_sec=native_timeout)
    # Harbor uses the agent build budget for separate verifiers, even when the
    # verifier's task environment declares a different build timeout.
    env.task_env_config.build_timeout_sec = 99
    build_finished = asyncio.Event()

    async def unavailable():
        await asyncio.Event().wait()

    async def build(command, **kwargs):
        env.commands.append(command)
        if command == ["build"]:
            await asyncio.sleep(0.075)
            build_finished.set()
        return ExecResult(return_code=0)

    monkeypatch.setattr(env, "_prepare_images", unavailable)
    monkeypatch.setattr(env, "_run_docker_compose_command", build)
    before = compute_task_digest(env.environment_dir.parent)
    await start_in_harbor(env, role, base_timeout=base_timeout, multiplier=multiplier)
    assert build_finished.is_set()
    assert ["up", "--detach", "--wait"] in env.commands
    assert compute_task_digest(env.environment_dir.parent) == before


@pytest.mark.anyio
@pytest.mark.parametrize("role,force_build", [("agent", False), ("agent", True), ("verifier", False)])
async def test_cache_allowance_does_not_extend_native_startup(
    make_environment, start_in_harbor, monkeypatch, caplog, role, force_build
):
    native_timeout, multiplier = registry_startup_timeouts(0.05, None, 2)
    env = make_environment(role=role, image_pull_timeout_sec=2, native_startup_timeout_sec=native_timeout)
    exceeded_native_budget = asyncio.Event()
    monkeypatch.setattr(env, "_prepare_images", AsyncMock(return_value=(role, [])))

    async def slow_start(self, force_build):
        await asyncio.sleep(0.25)
        exceeded_native_budget.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DockerEnvironment, "start", slow_start)
    expected_error = EnvironmentStartTimeoutError if role == "agent" else TimeoutError
    with pytest.raises(expected_error) as error:
        await asyncio.wait_for(
            start_in_harbor(env, role, base_timeout=0.05, multiplier=multiplier, force_build=force_build), timeout=0.6
        )
    assert not exceeded_native_budget.is_set()
    native_error = error.value.__cause__ if role == "agent" else error.value
    assert "Native Docker startup timed out after 0.05 seconds" in native_error.__notes__
    assert "phase=native_startup timeout_seconds=0.05" in caplog.text


@pytest.mark.anyio
async def test_upstream_timeout_is_not_reported_as_native_deadline(make_environment, monkeypatch, caplog):
    env = make_environment()
    monkeypatch.setattr(env, "_prepare_images", AsyncMock(return_value=("agent", [])))
    upstream_error = TimeoutError("upstream timeout")
    monkeypatch.setattr(DockerEnvironment, "start", AsyncMock(side_effect=upstream_error))
    with pytest.raises(TimeoutError) as error:
        await env.start(force_build=False)
    assert error.value is upstream_error
    assert not getattr(error.value, "__notes__", [])
    assert "phase=native_startup" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["agent", "verifier"])
@pytest.mark.parametrize("phase", ["preparation", "native"])
async def test_external_cancellation_survives_both_deadlines(
    make_environment, start_in_harbor, monkeypatch, caplog, role, phase
):
    native_timeout, multiplier = registry_startup_timeouts(1, None, 1)
    env = make_environment(role=role, image_pull_timeout_sec=1, native_startup_timeout_sec=native_timeout)
    entered = asyncio.Event()
    native = AsyncMock()

    async def hang(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(env, "_prepare_images", hang if phase == "preparation" else AsyncMock(return_value=(role, [])))
    monkeypatch.setattr(DockerEnvironment, "start", native if phase == "preparation" else hang)
    running = asyncio.create_task(start_in_harbor(env, role, base_timeout=1, multiplier=multiplier))
    await asyncio.wait_for(entered.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    if phase == "preparation":
        native.assert_not_awaited()
    assert "phase=native_startup" not in caplog.text


class Docker:
    def __init__(self, cached=False, pull_error=False, wrong_labels=False, architecture="amd64"):
        self.cached = cached
        self.pull_error = pull_error
        self.wrong_labels = wrong_labels
        self.architecture = architecture
        self.pulled = set()
        self.calls = []

    async def __call__(self, *args, env=None):
        self.calls.append(args)
        if args[0] == "version":
            return 0, f"linux/{self.architecture}"
        reference = args[-1]
        if args[0] == "pull":
            self.pulled.add(reference)
            return (1 if self.pull_error else 0), ""
        if not self.cached and reference not in self.pulled:
            return 1, ""
        role, service_tag = reference.rsplit("/", 2)[-2:]
        labels = image_labels(DIGEST, role, service_tag.split(":")[0])
        if self.wrong_labels:
            labels["org.ridges.task.digest"] = "wrong"
        return 0, json.dumps(
            [
                {
                    "Config": {"Labels": labels},
                    "Os": "linux",
                    "Architecture": self.architecture,
                    "RepoDigests": [reference.rsplit(":", 1)[0] + "@" + IMAGE_DIGEST],
                }
            ]
        )


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["agent", "verifier"])
@pytest.mark.parametrize("cached", [False, True])
async def test_native_harbor_uses_published_images_without_building_or_mutating_task(
    make_environment, monkeypatch, role, cached
):
    env = make_environment(role=role, sidecar=True)
    before = compute_task_digest(env.environment_dir.parent)
    original_config = env.task_env_config
    docker = Docker(cached=cached)
    monkeypatch.setattr(registry_module, "_docker", docker)
    await env.start(force_build=False)
    assert ["build"] not in env.commands
    assert ["up", "--detach", "--wait"] in env.commands
    assert env._use_prebuilt
    assert original_config.docker_image is None
    selected = yaml.safe_load(env._environment_docker_compose_path.read_text())
    assert "build" not in selected["services"]["db"]
    assert selected["services"]["db"]["pull_policy"] == "never"
    assert selected["services"]["main"]["environment"] == {"UNCHANGED": "yes"}
    assert selected["services"]["redis"] == {"image": "redis:7"}
    assert f"/{role}/main@{IMAGE_DIGEST}" in env.task_env_config.docker_image
    assert len(docker.pulled) == (0 if cached else 2)
    assert compute_task_digest(env.environment_dir.parent) == before
    generated = env._environment_docker_compose_path
    await env.stop(delete=True)
    assert not generated.exists()
    assert env.task_env_config is original_config


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing_or_unreachable", "wrong_labels", "unsupported_architecture"])
async def test_cache_failure_uses_untouched_native_build(make_environment, monkeypatch, failure):
    env = make_environment(sidecar=True)
    docker = Docker(
        pull_error=failure == "missing_or_unreachable",
        cached=failure == "wrong_labels",
        wrong_labels=failure == "wrong_labels",
        architecture="s390x" if failure == "unsupported_architecture" else "amd64",
    )
    monkeypatch.setattr(registry_module, "_docker", docker)
    await env.start(force_build=False)
    assert ["build"] in env.commands
    assert env.task_env_config.docker_image is None
    assert env._registry_compose_path is None
    assert "build" in yaml.safe_load(env._environment_docker_compose_path.read_text())["services"]["db"]


@pytest.mark.anyio
async def test_partial_image_success_does_not_change_fallback(make_environment, monkeypatch):
    env = make_environment(sidecar=True)
    docker = Docker()

    async def partial(*args, env=None):
        if args[0] == "pull" and "/main:" in args[-1]:
            return 1, ""
        return await docker(*args)

    monkeypatch.setattr(registry_module, "_docker", partial)
    await env.start(force_build=False)
    assert ["build"] in env.commands
    assert env._registry_compose_path is None
    assert len(docker.pulled) == 1


@pytest.mark.anyio
async def test_timeout_falls_back_but_cancellation_propagates(make_environment, monkeypatch):
    env = make_environment(image_pull_timeout_sec=0.01)
    entered = asyncio.Event()

    async def hang(*args, env=None):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(registry_module, "_docker", hang)
    await env.start(force_build=False)
    assert ["build"] in env.commands
    env.commands.clear()
    env._image_pull_timeout_sec = 100
    entered.clear()
    running = asyncio.create_task(env.start(force_build=False))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert env.commands == []


@pytest.mark.anyio
async def test_force_build_bypasses_registry_even_on_restarted_environment(make_environment, monkeypatch):
    env = make_environment()
    monkeypatch.setattr(registry_module, "_docker", Docker(cached=True))
    await env.start(force_build=False)
    monkeypatch.setattr(registry_module, "_docker", AsyncMock(side_effect=AssertionError("unexpected registry access")))
    env.commands.clear()
    await env.start(force_build=True)
    assert ["build"] in env.commands
    assert env.task_env_config.docker_image is None


@pytest.mark.anyio
async def test_container_start_failure_does_not_retry_with_build(make_environment, monkeypatch):
    env = make_environment()
    monkeypatch.setattr(registry_module, "_docker", Docker(cached=True))

    async def broken(command, **kwargs):
        env.commands.append(command)
        if command[0] == "up":
            raise RuntimeError("container start failed")
        return ExecResult(return_code=0)

    monkeypatch.setattr(env, "_run_docker_compose_command", broken)
    with pytest.raises(RuntimeError, match="container start failed"):
        await env.start(force_build=False)
    assert ["build"] not in env.commands
    assert sum(command[0] == "up" for command in env.commands) == 1


@pytest.mark.anyio
async def test_cancelled_docker_command_is_killed_and_reaped(monkeypatch):
    entered = asyncio.Event()

    async def communicate():
        entered.set()
        await asyncio.Event().wait()

    process = SimpleNamespace(
        returncode=None, communicate=communicate, wait=AsyncMock(), kill=lambda: setattr(process, "returncode", -9)
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    running = asyncio.create_task(registry_module._docker("pull", "example/image"))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert process.returncode == -9
    process.wait.assert_awaited_once()


@pytest.mark.anyio
async def test_concurrent_trials_share_one_pull(make_environment, monkeypatch):
    token_requests = []

    async def token_provider(repository):
        token_requests.append(repository)
        return "token"

    monkeypatch.setattr(registry_module, "_token_provider", token_provider)
    docker = Docker()

    async def slow_pull(*args, env=None):
        if args[0] == "pull":
            await asyncio.sleep(0.02)
        return await docker(*args)

    monkeypatch.setattr(registry_module, "_docker", slow_pull)
    first, second = make_environment(), make_environment()
    await asyncio.gather(first.start(force_build=False), second.start(force_build=False))
    assert sum(args[0] == "pull" for args in docker.calls) == 1
    assert len(token_requests) == 1
    assert first.task_env_config.docker_image == second.task_env_config.docker_image


@pytest.mark.anyio
async def test_native_prebuilt_task_avoids_registry(make_environment, monkeypatch):
    env = make_environment()
    env.task_env_config.docker_image = "upstream/image:version"
    env._env_vars.prebuilt_image_name = env.task_env_config.docker_image
    env._original_prebuilt_image_name = env.task_env_config.docker_image
    monkeypatch.setattr(registry_module, "_docker", AsyncMock(side_effect=AssertionError("unexpected registry access")))
    await env.start(force_build=False)
    assert ["build"] not in env.commands
    assert env.task_env_config.docker_image == "upstream/image:version"


@pytest.mark.anyio
async def test_dockerfile_only_context_and_cleanup(make_environment, monkeypatch):
    env = make_environment(role="verifier")
    (env.environment_dir / "docker-compose.yaml").unlink()
    monkeypatch.setattr(registry_module, "_docker", Docker(cached=True))
    before = compute_task_digest(env.environment_dir)
    await env.start(force_build=False)
    assert ["build"] not in env.commands
    assert compute_task_digest(env.environment_dir) == before
    assert not (env.environment_dir / "docker-compose.yaml").exists()


@pytest.mark.anyio
async def test_platform_override_keeps_native_behavior(make_environment, monkeypatch):
    env = make_environment()
    monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/arm64")
    docker = Docker(cached=True)
    monkeypatch.setattr(registry_module, "_docker", docker)
    await env.start(force_build=False)
    assert ["build"] in env.commands
    assert all(args[0] == "version" for args in docker.calls)


@pytest.mark.anyio
async def test_real_compose_resolves_pulled_images_without_build_entries(make_environment, monkeypatch):
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable; this check never contacts the daemon")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
    if version.returncode:
        pytest.skip("Docker Compose CLI is unavailable")
    env = make_environment(sidecar=True)
    monkeypatch.setattr(registry_module, "_docker", Docker(cached=True))
    await env.start(force_build=False)
    command = ["docker", "compose", "--project-directory", str(env.environment_dir)]
    for path in env._docker_compose_paths:
        command.extend(["-f", str(path)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(command, env=env._compose_env_vars(), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("main", "db"):
        assert "build" not in services[name]
        assert services[name]["pull_policy"] == "never"
        assert services[name]["image"].endswith("@" + IMAGE_DIGEST)


@pytest.mark.anyio
@pytest.mark.parametrize("provider", [True, False])
async def test_pull_authenticates_with_a_private_repository_token_only_when_a_provider_is_registered(
    make_environment, monkeypatch, provider
):
    requested = []

    async def token_provider(repository):
        requested.append(repository)
        return f"token-for-{repository}"

    monkeypatch.setattr(registry_module, "_token_provider", token_provider if provider else None)
    docker = Docker()
    pulls = []

    async def recording(*args, env=None):
        if args[0] == "pull":
            if env is None:
                pulls.append(None)
            else:
                config_dir = Path(env["DOCKER_CONFIG"])
                config = json.loads((config_dir / "config.json").read_text())
                pulls.append((config_dir, config, config_dir.stat().st_mode & 0o777))
        else:
            # Only the pull talks to the registry; local commands use the host config.
            assert env is None
        return await docker(*args)

    monkeypatch.setattr(registry_module, "_docker", recording)
    env = make_environment()
    await env.start(force_build=False)
    assert env._use_prebuilt
    if not provider:
        assert pulls == [None]
        return
    repository = f"tasks/v1/{'a' * 64}/agent/main"
    assert requested == [repository]
    ((config_dir, config, mode),) = pulls
    assert config == {"auths": {REGISTRY: {"registrytoken": f"token-for-{repository}"}}}
    assert mode == 0o700
    assert not config_dir.exists()


@pytest.mark.anyio
async def test_cached_image_needs_no_token(make_environment, monkeypatch):
    provider = AsyncMock(side_effect=AssertionError("unexpected token request"))
    monkeypatch.setattr(registry_module, "_token_provider", provider)
    monkeypatch.setattr(registry_module, "_docker", Docker(cached=True))
    env = make_environment()
    await env.start(force_build=False)
    assert env._use_prebuilt
    provider.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [("error", "token_unavailable"), (None, "token_unavailable"), ("", "token_unavailable"), ("hang", "TimeoutError")],
)
async def test_token_failure_falls_back_without_pulling_or_logging_details(
    make_environment, monkeypatch, caplog, failure, reason
):
    caplog.set_level(logging.INFO)

    async def failing_provider(repository):
        if failure == "error":
            raise RuntimeError("platform said no-such-detail")
        if failure == "hang":
            await asyncio.Event().wait()
        return failure

    monkeypatch.setattr(registry_module, "_token_provider", failing_provider)
    docker = Docker()
    monkeypatch.setattr(registry_module, "_docker", docker)
    env = make_environment(image_pull_timeout_sec=0.01)
    await env.start(force_build=False)
    assert ["build"] in env.commands
    assert not docker.pulled
    assert f"status=fallback reason={reason}" in caplog.text
    assert "no-such-detail" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            "Error response from daemon: manifest for {image} not found: manifest unknown: manifest unknown",
            "pull_missing",
        ),
        (
            'Error response from daemon: Head "https://{registry}/v2/x/manifests/linux-amd64": no basic auth credentials',
            "pull_denied",
        ),
        ("Error response from daemon: unauthorized: authentication required", "pull_denied"),
        (
            'Error response from daemon: Get "https://{registry}/v2/": dial tcp: lookup {registry}: no such host',
            "pull_failed",
        ),
    ],
)
async def test_pull_failure_reason_is_classified_without_logging_registry_output(
    make_environment, monkeypatch, caplog, error, reason
):
    caplog.set_level(logging.INFO)
    docker = Docker()

    async def failing_pull(*args, env=None):
        if args[0] == "pull":
            return 1, error.format(image=args[-1], registry=REGISTRY)
        return await docker(*args)

    monkeypatch.setattr(registry_module, "_docker", failing_pull)
    env = make_environment()
    await env.start(force_build=False)
    assert ["build"] in env.commands
    assert f"status=fallback reason={reason}" in caplog.text
    assert "Error response" not in caplog.text
