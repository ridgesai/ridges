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
from ridges_harbor._stdlib_contract import RUN_LOG_FILENAME, SETUP_LOG_FILENAME
from ridges_harbor.agents import MinerRuntimeError, RidgesMinerAgent
from ridges_harbor.runner import _run_task_dir


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
    ):
        self.env = env or {}
        self.type = type
        self.import_path = import_path


class FakeJobConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.environment = kwargs.get(
            "environment",
            FakeEnvironmentConfig(),
        )


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

    monkeypatch.setitem(sys.modules, "harbor", harbor_module)
    monkeypatch.setitem(sys.modules, "harbor.environments", environments_module)
    monkeypatch.setitem(sys.modules, "harbor.environments.factory", environments_factory_module)
    monkeypatch.setitem(sys.modules, "harbor.job", job_module)
    monkeypatch.setitem(sys.modules, "harbor.models", models_module)
    monkeypatch.setitem(sys.modules, "harbor.models.job", job_package_module)
    monkeypatch.setitem(sys.modules, "harbor.models.job.config", job_config_module)
    monkeypatch.setitem(sys.modules, "harbor.models.trial", trial_package_module)
    monkeypatch.setitem(sys.modules, "harbor.models.trial.config", trial_config_module)


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
        upstream_url="http://127.0.0.1:1234",
        upstream_host="127.0.0.1",
        results_dir=results_dir,
        debug=False,
        job_name="job-1",
    )

    assert FakeEnvironmentFactory.calls == [("docker", None)]
    assert FakeJob.created_configs[0].tasks[0].path == task_dir
    assert FakeJob.created_configs[0].agents[0].override_timeout_sec == 30.0
    assert FakeJob.created_configs[0].agents[0].kwargs == {
        "agent_path": str(tmp_path / "agent.py"),
    }
    assert FakeJob.created_configs[0].agents[0].env == {
        "EVALUATION_RUN_ID": "eval-run-1",
        "SANDBOX_PROXY_URL": runner_module.DEFAULT_AGENT_SANDBOX_PROXY_URL,
        "AGENT_TIMEOUT": "30",
    }
    assert FakeJob.created_configs[0].environment.env == {
        "RIDGES_TRIAL_ID": FakeJob.created_configs[0].environment.env["RIDGES_TRIAL_ID"],
        "RIDGES_HARBOR_UPSTREAM_URL": "http://127.0.0.1:1234",
        "RIDGES_HARBOR_UPSTREAM_HOST": "127.0.0.1",
        "DOCKER_BUILDKIT": os.environ.get("DOCKER_BUILDKIT", "0"),
        "COMPOSE_DOCKER_CLI_BUILD": os.environ.get("COMPOSE_DOCKER_CLI_BUILD", "0"),
        "COMPOSE_BAKE": os.environ.get("COMPOSE_BAKE", "false"),
    }
    assert len(FakeJob.last_instance.agent_started_hooks) == 0
    assert len(FakeJob.last_instance.verification_started_hooks) == 1
    assert len(FakeJob.last_instance.ended_hooks) == 0
    assert summary.trial_result is FakeJob.last_instance._trial_result
    assert summary.trial_result.exception_info.occurred_at == "2026-04-09T09:14:51.454327"
    assert os.environ == original_environ


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
