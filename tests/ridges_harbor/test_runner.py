import asyncio
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext

import ridges_harbor.runner as runner_module
from models.openrouter import OpenRouterRuntimeConfig
from ridges_harbor._stdlib_contract import (
    GIT_BASELINE_LOG_FILENAME,
    PATCH_APPLY_LOG_FILENAME,
    PATCH_CHECK_LOG_FILENAME,
    PATCH_PUBLISH_LOG_FILENAME,
    PATCH_PUBLISHED_METADATA_KEY,
    RUN_LOG_FILENAME,
    SETUP_LOG_FILENAME,
)
from ridges_harbor.agents import MinerRuntimeError, RidgesMinerAgent
from ridges_harbor.docker_runtime import docker_environment_env
from ridges_harbor.runner import _run_task_dir


def test_docker_environment_defaults_to_buildkit_bake(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DOCKER_BUILDKIT", raising=False)
    monkeypatch.delenv("COMPOSE_BAKE", raising=False)

    env = docker_environment_env(
        ridges_trial_id="trial-1",
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        evaluation_run_id="eval-1",
        max_cost_usd="1",
        proxy_data_dir=str(tmp_path),
        openrouter_config=None,
    )

    assert env["DOCKER_BUILDKIT"] == "1"
    assert env["COMPOSE_BAKE"] == "true"


class FakeTaskConfig:
    def __init__(self, *, path: Path):
        self.path = path


class FakeRetryConfig:
    def __init__(self, *, max_retries: int):
        self.max_retries = max_retries


class FakeAgentConfig:
    def __init__(
        self,
        *,
        import_path: str,
        kwargs: dict,
        override_timeout_sec: float | None = None,
        env: dict[str, str] | None = None,
    ):
        self.import_path = import_path
        self.kwargs = kwargs
        self.override_timeout_sec = override_timeout_sec
        self.env = env or {}


class FakeEnvironmentConfig:
    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        type: str = "docker",
        import_path: str | None = None,
        kwargs: dict[str, object] | None = None,
    ):
        self.env = env or {}
        self.type = type
        self.import_path = import_path
        self.kwargs = kwargs or {}


class FakeVerifierConfig:
    def __init__(
        self,
        *,
        max_timeout_sec: float | None = None,
        override_timeout_sec: float | None = None,
        disable: bool = False,
        import_path: str | None = None,
        kwargs: dict | None = None,
    ):
        self.max_timeout_sec = max_timeout_sec
        self.override_timeout_sec = override_timeout_sec
        self.disable = disable
        self.import_path = import_path
        self.kwargs = kwargs or {}


class FakeJobConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.environment = kwargs.get(
            "environment",
            FakeEnvironmentConfig(),
        )
        self.verifier = kwargs.get("verifier", FakeVerifierConfig())


class FakeEnvironmentFactory:
    calls: list[tuple[str, str]] = []

    @classmethod
    def run_preflight(cls, *, type: str, import_path: str) -> None:
        cls.calls.append((type, import_path))


class FakeTrialResult:
    trial_name = "trial-1"

    def __init__(self) -> None:
        self.exception_info = SimpleNamespace(occurred_at="2026-04-09T09:14:51.454327")


class FakeJobResult:
    def __init__(self, trial_result: FakeTrialResult) -> None:
        self.trial_results = [trial_result]


class FakeJob:
    created_configs = []
    last_instance = None

    def __init__(self, config: FakeJobConfig) -> None:
        self.config = config
        self.job_dir = config.jobs_dir / config.job_name
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._trial_result = FakeTrialResult()
        self.agent_started_hooks = []
        self.verification_started_hooks = []
        self.ended_hooks = []
        FakeJob.last_instance = self

    @classmethod
    async def create(cls, config: FakeJobConfig) -> "FakeJob":
        cls.created_configs.append(config)
        return cls(config)

    def on_agent_started(self, callback):
        self.agent_started_hooks.append(callback)
        return self

    def on_verification_started(self, callback):
        self.verification_started_hooks.append(callback)
        return self

    def on_trial_ended(self, callback):
        self.ended_hooks.append(callback)
        return self

    async def run(self) -> FakeJobResult:
        return FakeJobResult(self._trial_result)


class FakeUploadEnvironment:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []

    async def upload_file(self, source: Path, destination: str) -> None:
        self.uploads.append((source, destination))


def _install_fake_harbor(monkeypatch) -> None:
    FakeEnvironmentFactory.calls = []
    FakeJob.created_configs = []
    FakeJob.last_instance = None

    harbor_module = types.ModuleType("harbor")
    environments_module = types.ModuleType("harbor.environments")
    environments_factory_module = types.ModuleType("harbor.environments.factory")
    environments_factory_module.EnvironmentFactory = FakeEnvironmentFactory

    job_module = types.ModuleType("harbor.job")
    job_module.Job = FakeJob

    models_module = types.ModuleType("harbor.models")
    job_package_module = types.ModuleType("harbor.models.job")
    job_config_module = types.ModuleType("harbor.models.job.config")
    job_config_module.JobConfig = FakeJobConfig
    job_config_module.RetryConfig = FakeRetryConfig

    trial_package_module = types.ModuleType("harbor.models.trial")
    trial_config_module = types.ModuleType("harbor.models.trial.config")
    trial_config_module.AgentConfig = FakeAgentConfig
    trial_config_module.EnvironmentConfig = FakeEnvironmentConfig
    trial_config_module.TaskConfig = FakeTaskConfig
    trial_config_module.VerifierConfig = FakeVerifierConfig

    monkeypatch.setitem(sys.modules, "harbor", harbor_module)
    monkeypatch.setitem(sys.modules, "harbor.environments", environments_module)
    monkeypatch.setitem(sys.modules, "harbor.environments.factory", environments_factory_module)
    monkeypatch.setitem(sys.modules, "harbor.job", job_module)
    monkeypatch.setitem(sys.modules, "harbor.models", models_module)
    monkeypatch.setitem(sys.modules, "harbor.models.job", job_package_module)
    monkeypatch.setitem(sys.modules, "harbor.models.job.config", job_config_module)
    monkeypatch.setitem(sys.modules, "harbor.models.trial", trial_package_module)
    monkeypatch.setitem(sys.modules, "harbor.models.trial.config", trial_config_module)
    monkeypatch.setattr(runner_module, "_resolve_separate_verifier", lambda _task_dir: False)


@pytest.mark.anyio
async def test_run_task_dir_uses_task_config_and_environment_env(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)
    results_dir = tmp_path / "results"
    original_environ = os.environ.copy()

    summary = await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-1",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=60.0,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=results_dir,
        debug=False,
        job_name="job-1",
    )

    assert FakeEnvironmentFactory.calls == [("docker", None)]
    assert FakeJob.created_configs[0].tasks[0].path == task_dir
    assert FakeJob.created_configs[0].verifier.max_timeout_sec == 60.0
    assert FakeJob.created_configs[0].verifier.import_path is None
    assert FakeJob.created_configs[0].artifacts == []
    assert FakeJob.created_configs[0].agents[0].override_timeout_sec == 30.0
    assert FakeJob.created_configs[0].agents[0].kwargs == {
        "agent_path": str(tmp_path / "agent.py"),
        "separate_verifier": False,
    }
    assert FakeJob.created_configs[0].agents[0].env == {
        "EVALUATION_RUN_ID": "eval-run-1",
        "RIDGES_MAX_COST_USD": "9",
        "SANDBOX_PROXY_URL": runner_module.DEFAULT_AGENT_SANDBOX_PROXY_URL,
        "AGENT_TIMEOUT": "30",
    }
    assert FakeJob.created_configs[0].environment.env == {
        "RIDGES_TRIAL_ID": FakeJob.created_configs[0].environment.env["RIDGES_TRIAL_ID"],
        "RIDGES_HARBOR_UPSTREAM_URL": "http://127.0.0.1:1234",
        "RIDGES_HARBOR_UPSTREAM_HOST": "127.0.0.1",
        "RIDGES_EVALUATION_RUN_ID": "eval-run-1",
        "RIDGES_MAX_COST_USD": "9",
        "RIDGES_PROXY_DATA_DIR": str(results_dir / "job-1" / "proxy_data"),
        "RIDGES_INFERENCE_SEED": "",
        "RIDGES_OPENROUTER_MANAGEMENT_KEY": "",
        "RIDGES_OPENROUTER_WORKSPACE_ID": "",
        "RIDGES_OPENROUTER_EXPECTED_API_KEY_SHA256": "",
        "DOCKER_BUILDKIT": os.environ.get("DOCKER_BUILDKIT", "1"),
        "COMPOSE_DOCKER_CLI_BUILD": os.environ.get("COMPOSE_DOCKER_CLI_BUILD", "0"),
        "COMPOSE_BAKE": os.environ.get("COMPOSE_BAKE", "true"),
    }
    assert (results_dir / "job-1" / "proxy_data").is_dir()
    assert len(FakeJob.last_instance.agent_started_hooks) == 0
    assert len(FakeJob.last_instance.verification_started_hooks) == 1
    assert len(FakeJob.last_instance.ended_hooks) == 0
    assert summary.trial_result is FakeJob.last_instance._trial_result
    assert summary.trial_result.exception_info.occurred_at == "2026-04-09T09:14:51.454327"
    assert os.environ == original_environ


@pytest.mark.anyio
async def test_run_task_dir_uses_loopback_proxy_in_kubernetes(tmp_path: Path, monkeypatch) -> None:
    from validator import config as validator_config

    _install_fake_harbor(monkeypatch)
    monkeypatch.setenv("RIDGES_ENVIRONMENT_TYPE", "kubernetes")
    monkeypatch.setattr(
        "ridges_harbor.k8s_environment.build_isolated_k8s_apis",
        lambda _context=None: (object(), object()),
    )
    # These are absent if another test imported validator.config in Docker mode.
    monkeypatch.setattr(validator_config, "K8S_MEMORY_REQUEST_FRACTION", 0.25, raising=False)
    monkeypatch.setattr(validator_config, "K8S_CPU_REQUEST_FRACTION", 0.25, raising=False)
    monkeypatch.setattr(validator_config, "K8S_MEMORY_LIMIT_MULTIPLIER", 1.0, raising=False)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)

    async def fetch_task_url(task_digest: str) -> str:
        assert task_digest == f"sha256:{'a' * 64}"
        return "https://tasks.example.test/task.tar.gz"

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        task_digest=f"sha256:{'a' * 64}",
        evaluation_run_id="eval-run-k8s",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=None,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=tmp_path / "results",
        debug=False,
        job_name="job-k8s",
        inference_seed=123,
        fetch_task_url=fetch_task_url,
    )

    config = FakeJob.created_configs[0]
    assert config.agents[0].env["SANDBOX_PROXY_URL"] == "http://127.0.0.1:8080"
    assert config.environment.import_path == "ridges_harbor.k8s_environment:RidgesKubernetesEnvironment"
    assert config.environment.kwargs["inference_seed"] == 123
    assert config.environment.kwargs["verifier_image_required"] is False
    assert config.environment.kwargs["task_dir"] == str(task_dir)
    assert runner_module.DEFAULT_AGENT_SANDBOX_PROXY_URL == "http://sandbox-proxy:80"


@pytest.mark.anyio
async def test_run_task_dir_prebuilds_kubernetes_verifier_image_for_separate_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from validator import config as validator_config

    _install_fake_harbor(monkeypatch)
    monkeypatch.setattr(runner_module, "_resolve_separate_verifier", lambda _task_dir: True)
    monkeypatch.setenv("RIDGES_ENVIRONMENT_TYPE", "kubernetes")
    monkeypatch.setattr(
        "ridges_harbor.k8s_environment.build_isolated_k8s_apis",
        lambda _context=None: (object(), object()),
    )
    monkeypatch.setattr(validator_config, "K8S_MEMORY_REQUEST_FRACTION", 0.25, raising=False)
    monkeypatch.setattr(validator_config, "K8S_CPU_REQUEST_FRACTION", 0.25, raising=False)
    monkeypatch.setattr(validator_config, "K8S_MEMORY_LIMIT_MULTIPLIER", 1.0, raising=False)

    task_dir = tmp_path / "dataset" / "native-separate-task"
    task_dir.mkdir(parents=True)

    async def fetch_task_url(_task_digest: str) -> str:
        return "https://tasks.example.test/task.tar.gz"

    await _run_task_dir(
        task_dir=task_dir,
        task_name="native-separate-task",
        task_digest=f"sha256:{'b' * 64}",
        evaluation_run_id="eval-run-k8s-separate",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=None,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=tmp_path / "results",
        debug=False,
        job_name="job-k8s-separate",
        fetch_task_url=fetch_task_url,
    )

    config = FakeJob.created_configs[0]
    assert config.agents[0].kwargs["separate_verifier"] is True
    assert config.environment.kwargs["verifier_image_required"] is True
    assert config.environment.kwargs["task_dir"] == str(task_dir)
    assert config.artifacts == ["/logs/agent/patch.diff"]
    assert config.verifier.import_path is None
    assert FakeJob.last_instance.verification_started_hooks == []


@pytest.mark.anyio
async def test_run_task_dir_passes_optional_openrouter_key_and_cost_cap(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)
    results_dir = tmp_path / "results"

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-2",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=None,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=results_dir,
        debug=False,
        job_name="job-2",
        openrouter_config=OpenRouterRuntimeConfig(
            api_key="sk-or-v1-secret",
            management_key="sk-or-mgmt-secret",
            workspace_id="workspace-1",
            expected_api_key_sha256="expected-hash",
        ),
        max_cost_usd=12.5,
        inference_seed=123,
    )

    assert FakeJob.created_configs[0].agents[0].env["OPENROUTER_API_KEY"] == "sk-or-v1-secret"
    assert FakeJob.created_configs[0].agents[0].env["RIDGES_MAX_COST_USD"] == "12.5"
    assert FakeJob.created_configs[0].environment.env["RIDGES_MAX_COST_USD"] == "12.5"
    assert FakeJob.created_configs[0].environment.env["RIDGES_INFERENCE_SEED"] == "123"
    assert FakeJob.created_configs[0].environment.env["RIDGES_OPENROUTER_MANAGEMENT_KEY"] == "sk-or-mgmt-secret"
    assert FakeJob.created_configs[0].environment.env["RIDGES_OPENROUTER_WORKSPACE_ID"] == "workspace-1"
    assert FakeJob.created_configs[0].environment.env["RIDGES_OPENROUTER_EXPECTED_API_KEY_SHA256"] == "expected-hash"


@pytest.mark.anyio
async def test_run_task_dir_registers_lifecycle_hooks_in_expected_order(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)
    results_dir = tmp_path / "results"

    async def on_agent_started(_event) -> None:
        return None

    async def on_verification_started(_event) -> None:
        return None

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-1",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=None,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=results_dir,
        debug=False,
        job_name="job-1",
        on_agent_started=on_agent_started,
        on_verification_started=on_verification_started,
    )

    assert FakeJob.last_instance.agent_started_hooks == [on_agent_started]
    assert len(FakeJob.last_instance.verification_started_hooks) == 2
    assert FakeJob.last_instance.verification_started_hooks[1] is on_verification_started


@pytest.mark.anyio
async def test_run_task_dir_leaves_separate_verifier_egress_to_harbor(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)
    monkeypatch.setattr(runner_module, "_resolve_separate_verifier", lambda _task_dir: True)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)

    async def on_verification_started(_event) -> None:
        return None

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-separate",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=None,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=tmp_path / "results",
        debug=False,
        job_name="job-separate",
        on_verification_started=on_verification_started,
    )

    assert FakeJob.last_instance.verification_started_hooks == [on_verification_started]
    config = FakeJob.created_configs[0]
    assert config.artifacts == ["/logs/agent/patch.diff"]
    assert config.verifier.import_path is None


@pytest.mark.anyio
async def test_exec_with_log_writes_agent_timeout_marker_on_cancellation(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    async def fake_exec_as_agent(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(miner, "exec_as_agent", fake_exec_as_agent)

    with pytest.raises(asyncio.CancelledError):
        await miner._exec_with_log(
            environment=SimpleNamespace(),
            executor=miner.exec_as_agent,
            command="python3 /installed-agent/ridges_miner_runtime.py",
            log_filename=RUN_LOG_FILENAME,
            cancelled_detail="agent execution was cancelled, likely due to timeout",
        )

    run_log = (tmp_path / "logs" / RUN_LOG_FILENAME).read_text()
    assert "[state] started" in run_log
    assert "[state] cancelled" in run_log
    assert "likely due to timeout" in run_log


@pytest.mark.anyio
async def test_exec_as_root_with_log_writes_timeout_marker_on_cancellation(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    async def fake_exec_as_root(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(miner, "exec_as_root", fake_exec_as_root)

    with pytest.raises(asyncio.CancelledError):
        await miner._exec_with_log(
            environment=SimpleNamespace(),
            executor=miner.exec_as_root,
            command="mkdir -p /logs/agent",
            log_filename=SETUP_LOG_FILENAME,
            cancelled_detail="command execution was cancelled",
        )

    setup_log = (tmp_path / "logs" / SETUP_LOG_FILENAME).read_text()
    assert "[state] started" in setup_log
    assert "[state] cancelled" in setup_log


@pytest.mark.anyio
async def test_exec_with_log_can_omit_output_body_on_success(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    async def fake_exec_as_agent(*args, **kwargs):
        return SimpleNamespace(return_code=0, stdout="runtime stdout", stderr="runtime stderr")

    monkeypatch.setattr(miner, "exec_as_agent", fake_exec_as_agent)

    await miner._exec_with_log(
        environment=SimpleNamespace(),
        executor=miner.exec_as_agent,
        command="python3 /installed-agent/ridges_miner_runtime.py",
        log_filename=RUN_LOG_FILENAME,
        cancelled_detail="agent execution was cancelled, likely due to timeout",
        include_output_body=False,
    )

    run_log = (tmp_path / "logs" / RUN_LOG_FILENAME).read_text()
    assert "$ python3 /installed-agent/ridges_miner_runtime.py" in run_log
    assert "[return_code] 0" in run_log
    assert "[stdout]" not in run_log
    assert "[stderr]" not in run_log
    assert "runtime stdout" not in run_log
    assert "runtime stderr" not in run_log


@pytest.mark.anyio
async def test_exec_with_log_translates_harbor_non_zero_exit_to_miner_error(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    async def fake_exec_as_agent(*args, **kwargs):
        raise NonZeroAgentExitCodeError("boom")

    monkeypatch.setattr(miner, "exec_as_agent", fake_exec_as_agent)

    with pytest.raises(MinerRuntimeError) as exc_info:
        await miner._exec_with_log(
            environment=SimpleNamespace(),
            executor=miner.exec_as_agent,
            command="python3 /installed-agent/ridges_miner_runtime.py",
            log_filename=RUN_LOG_FILENAME,
            cancelled_detail="agent execution was cancelled, likely due to timeout",
            error_summary="Legacy miner runtime failed",
            error_type=MinerRuntimeError,
        )

    assert "Legacy miner runtime failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, NonZeroAgentExitCodeError)
    run_log = (tmp_path / "logs" / RUN_LOG_FILENAME).read_text()
    assert "[exception]" in run_log


@pytest.mark.anyio
async def test_run_renders_instruction_with_prompt_template(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    prompt_template_path = tmp_path / "prompt.jinja"
    prompt_template_path.write_text("prefix {{ instruction }} suffix")

    miner = RidgesMinerAgent(
        logs_dir=tmp_path / "logs",
        agent_path=str(agent_path),
        prompt_template_path=prompt_template_path,
    )

    uploaded_instruction: dict[str, str] = {}

    class FakeEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            if destination == miner._env_instruction_path:
                uploaded_instruction["content"] = Path(source).read_text()

    async def fake_exec_with_log(*args, **kwargs):
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)

    await miner.run(
        "original instruction",
        environment=FakeEnvironment(),
        context=AgentContext(),
    )

    assert uploaded_instruction["content"] == "prefix original instruction suffix"


@pytest.mark.anyio
async def test_run_ensures_git_baseline_before_runtime_and_patch_apply(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path), workdir="/task-workdir")
    calls: list[dict[str, object]] = []

    class FakeEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            return None

    async def fake_exec_with_log(
        environment,
        *,
        executor,
        command,
        log_filename,
        cancelled_detail,
        cwd=None,
        error_summary=None,
        error_type=None,
        include_output_body=True,
    ):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "error_summary": error_summary,
                "error_type": error_type,
                "include_output_body": include_output_body,
                "log_filename": log_filename,
            }
        )
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)

    context = AgentContext()
    await miner.run(
        "fix the task",
        environment=FakeEnvironment(),
        context=context,
    )

    assert [call["log_filename"] for call in calls] == [
        GIT_BASELINE_LOG_FILENAME,
        RUN_LOG_FILENAME,
        PATCH_CHECK_LOG_FILENAME,
        PATCH_APPLY_LOG_FILENAME,
        PATCH_PUBLISH_LOG_FILENAME,
    ]
    assert all(call["cwd"] == "/task-workdir" for call in calls)

    baseline_command = str(calls[0]["command"])
    assert "command -v git" in baseline_command
    assert "git rev-parse HEAD" in baseline_command
    assert "git rev-parse --is-inside-work-tree" not in baseline_command
    assert "git config commit.gpgsign false" in baseline_command
    assert "git commit --allow-empty -m 'ridges baseline'" in baseline_command
    assert calls[0]["error_summary"] == "Failed to initialize git baseline"
    assert calls[0]["error_type"] is MinerRuntimeError
    assert calls[1]["include_output_body"] is False
    assert miner._env_raw_patch_path in str(calls[1]["command"])
    assert miner._env_raw_patch_path in str(calls[2]["command"])
    assert miner._env_patch_path not in str(calls[2]["command"])
    assert miner._env_raw_patch_path in str(calls[3]["command"])
    assert miner._env_patch_path not in str(calls[3]["command"])
    assert miner._env_raw_patch_path in str(calls[4]["command"])
    assert miner._env_patch_path in str(calls[4]["command"])
    assert context.metadata == {PATCH_PUBLISHED_METADATA_KEY: True}


@pytest.mark.anyio
async def test_run_does_not_publish_patch_when_apply_is_cancelled(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))
    calls: list[str] = []

    class FakeEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            return None

    async def fake_exec_with_log(*args, **kwargs):
        log_filename = str(kwargs["log_filename"])
        calls.append(log_filename)
        if log_filename == PATCH_APPLY_LOG_FILENAME:
            raise asyncio.CancelledError()
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)

    context = AgentContext()
    with pytest.raises(asyncio.CancelledError):
        await miner.run("fix the task", environment=FakeEnvironment(), context=context)

    assert PATCH_APPLY_LOG_FILENAME in calls
    assert PATCH_PUBLISH_LOG_FILENAME not in calls
    assert not context.metadata


@pytest.mark.anyio
async def test_separate_run_publishes_without_applying_in_agent_worktree(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(
        logs_dir=tmp_path / "logs",
        agent_path=str(agent_path),
        separate_verifier=True,
    )
    calls: list[str] = []

    class FakeEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            return None

    async def fake_exec_with_log(*args, **kwargs):
        calls.append(str(kwargs["log_filename"]))
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)

    context = AgentContext()
    await miner.run("fix the task", environment=FakeEnvironment(), context=context)

    assert calls == [
        GIT_BASELINE_LOG_FILENAME,
        RUN_LOG_FILENAME,
        PATCH_CHECK_LOG_FILENAME,
        PATCH_PUBLISH_LOG_FILENAME,
    ]
    assert context.metadata == {PATCH_PUBLISHED_METADATA_KEY: True}


@pytest.mark.anyio
async def test_run_does_not_mark_patch_published_when_publish_is_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    class FakeEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            return None

    async def fake_exec_with_log(*args, **kwargs):
        if kwargs["log_filename"] == PATCH_PUBLISH_LOG_FILENAME:
            raise asyncio.CancelledError()
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)
    context = AgentContext()

    with pytest.raises(asyncio.CancelledError):
        await miner.run("fix the task", environment=FakeEnvironment(), context=context)

    assert not context.metadata


@pytest.mark.anyio
async def test_install_uploads_stdlib_contract_beside_runtime(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))
    environment = FakeUploadEnvironment()

    async def fake_exec_with_log(*args, **kwargs):
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)
    monkeypatch.setattr(miner, "_bootstrap_runtime_dependencies", fake_exec_with_log)

    await miner.install(environment)

    uploaded_destinations = {destination for _, destination in environment.uploads}
    assert miner._env_agent_path in uploaded_destinations
    assert miner._env_runtime_path in uploaded_destinations
    assert miner._env_stdlib_contract_path in uploaded_destinations


@pytest.mark.anyio
async def test_install_makes_uploaded_miner_source_readable_by_agent_user(tmp_path: Path) -> None:
    """agent.py arrives 0600 root-owned (tempfile mode survives upload); a task whose
    [agent] user is non-root then cannot read it, so install() must chmod it as root."""
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))

    events: list[tuple[str, str, str | None]] = []

    class RecordingEnvironment:
        async def upload_file(self, source: Path, destination: str) -> None:
            events.append(("upload", destination, None))

        async def exec(self, command: str, user=None, env=None, cwd=None, timeout_sec=None):
            events.append(("exec", command, user))
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    await miner.install(RecordingEnvironment())

    upload_index = events.index(("upload", miner._env_agent_path, None))
    chmod_events = [
        (index, user)
        for index, (kind, command, user) in enumerate(events)
        if kind == "exec" and command.endswith(f"chmod 0755 {miner.runtime_dir} && chmod 0444 {miner._env_agent_path}")
    ]
    assert chmod_events, f"no chmod of the miner source among: {events}"
    chmod_index, chmod_user = chmod_events[0]
    assert chmod_user == "root"
    assert chmod_index > upload_index


def test_runtime_script_runs_from_uploaded_sibling_stdlib_contract_only(tmp_path: Path) -> None:
    runtime_source = Path(__file__).resolve().parents[2] / "ridges_harbor" / "ridges_miner_runtime.py"
    contract_source = Path(__file__).resolve().parents[2] / "ridges_harbor" / "_stdlib_contract.py"
    runtime_path = tmp_path / "ridges_miner_runtime.py"
    contract_path = tmp_path / "_stdlib_contract.py"
    runtime_path.write_text(runtime_source.read_text())
    contract_path.write_text(contract_source.read_text())

    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return 'diff --git a/a b/a\\n'\n")
    instruction_path = tmp_path / "instruction.md"
    instruction_path.write_text("fix the bug")
    patch_path = tmp_path / "patch.diff"
    runtime_payload_path = tmp_path / "ridges_runtime.json"

    completed = subprocess.run(
        [
            sys.executable,
            "ridges_miner_runtime.py",
            "--agent",
            str(agent_path),
            "--instruction",
            str(instruction_path),
            "--patch",
            str(patch_path),
            "--runtime",
            str(runtime_payload_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
    )

    assert completed.returncode == 0
    assert patch_path.read_text() == "diff --git a/a b/a\n"
    assert runtime_payload_path.exists() is False


@pytest.mark.anyio
async def test_run_task_dir_forwards_environment_build_timeout_multiplier(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-1",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=60.0,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=tmp_path / "results",
        debug=False,
        job_name="job-1",
        environment_build_timeout_multiplier=2.5,
    )

    assert FakeJob.created_configs[0].environment_build_timeout_multiplier == 2.5


@pytest.mark.anyio
async def test_run_task_dir_defaults_environment_build_timeout_multiplier_to_none(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    task_dir = tmp_path / "dataset" / "update-status-file"
    task_dir.mkdir(parents=True)

    await _run_task_dir(
        task_dir=task_dir,
        task_name="update-status-file",
        evaluation_run_id="eval-run-1",
        agent_path=tmp_path / "agent.py",
        agent_timeout_sec=30.0,
        verifier_timeout_sec=60.0,
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=tmp_path / "results",
        debug=False,
        job_name="job-1",
    )

    assert FakeJob.created_configs[0].environment_build_timeout_multiplier is None
