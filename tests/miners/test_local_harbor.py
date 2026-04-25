import os
import sys
import tarfile
import time
import types
from pathlib import Path

import pytest

import miners.local_harbor as local_harbor_module
from miners.inference_client import LocalInferenceConfig
from miners.local_harbor import CustomSandboxProxyConfig


class FakeTaskConfig:
    def __init__(self, *, path: Path):
        self.path = path


class FakeRetryConfig:
    def __init__(self, *, max_retries: int):
        self.max_retries = max_retries


class FakeAgentConfig:
    def __init__(self, *, import_path: str, kwargs: dict, override_timeout_sec=None, env=None):
        self.import_path = import_path
        self.kwargs = kwargs
        self.override_timeout_sec = override_timeout_sec
        self.env = env or {}


class FakeEnvironmentConfig:
    def __init__(self, *, env=None, type: str = "docker", import_path: str | None = None):
        self.env = env or {}
        self.type = type
        self.import_path = import_path


class FakeJobConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.environment = kwargs.get("environment", FakeEnvironmentConfig())


class FakeEnvironmentFactory:
    calls: list[tuple[str, str | None]] = []

    @classmethod
    def run_preflight(cls, *, type: str, import_path: str | None) -> None:
        cls.calls.append((type, import_path))


class FakeTrialResult:
    trial_name = "trial-1"

    def __init__(self) -> None:
        self.exception_info = None


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

    async def run(self) -> FakeJobResult:
        return FakeJobResult(self._trial_result)


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


def _write_agent(agent_path: Path) -> None:
    agent_path.write_text("def agent_main(input):\n    return 'diff --git a/a b/a\\n'\n")


def _write_harbor_task(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "instruction.md").write_text("fix the bug\n")
    (task_dir / "task.toml").write_text("[task]\nname = 'example'\n")
    (task_dir / "environment").mkdir()
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text("#!/bin/sh\nexit 0\n")


def _inference(provider: str = "openrouter") -> LocalInferenceConfig:
    if provider == "chutes":
        return LocalInferenceConfig(
            provider="chutes",
            api_key="secret",
            base_url="https://llm.chutes.ai/v1",
            embedding_base_url="https://embed.chutes.ai/v1",
        )
    if provider == "targon":
        return LocalInferenceConfig(provider="targon", api_key="secret", base_url="https://targon.example/v1")
    return LocalInferenceConfig(provider="openrouter", api_key="secret")


def _custom_proxy() -> CustomSandboxProxyConfig:
    return CustomSandboxProxyConfig(sandbox_proxy_url="https://proxy.example")


@pytest.mark.anyio
async def test_run_local_task_injects_provider_env_without_scaffold(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)
    pruned = []

    async def fake_prune() -> None:
        pruned.append(True)

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)

    task_dir = tmp_path / "task"
    _write_harbor_task(task_dir)
    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    summary = await local_harbor_module.run_local_task(
        task_dir,
        agent_path=agent_path,
        inference=_inference(),
        evaluation_run_id="eval-run-1",
        agent_timeout_sec=30.0,
        results_dir=tmp_path / "results",
        job_name="job-1",
    )

    assert FakeEnvironmentFactory.calls == [("docker", None)]
    assert FakeJob.created_configs[0].tasks[0].path == task_dir
    assert FakeJob.created_configs[0].environment.env == {}
    assert FakeJob.created_configs[0].agents[0].import_path == "miners.local_agent:LocalMinerAgent"
    assert FakeJob.created_configs[0].agents[0].kwargs == {"agent_path": str(agent_path.resolve())}
    assert FakeJob.created_configs[0].agents[0].env == {
        "EVALUATION_RUN_ID": "eval-run-1",
        "RIDGES_INFERENCE_PROVIDER": "openrouter",
        "RIDGES_INFERENCE_API_KEY": "secret",
        "RIDGES_INFERENCE_BASE_URL": "https://openrouter.ai/api/v1",
        "RIDGES_INFERENCE_EMBEDDING_BASE_URL": "https://openrouter.ai/api/v1",
        "AGENT_TIMEOUT": "30",
    }
    assert "SANDBOX_PROXY_URL" not in FakeJob.created_configs[0].agents[0].env
    assert summary.task_dir == task_dir
    assert pruned == [True]


@pytest.mark.anyio
async def test_run_local_task_injects_custom_proxy_env_without_provider_vars(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    async def fake_prune() -> None:
        return None

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)

    task_dir = tmp_path / "task"
    _write_harbor_task(task_dir)
    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    await local_harbor_module.run_local_task(
        task_dir,
        agent_path=agent_path,
        inference=_custom_proxy(),
        evaluation_run_id="eval-run-2",
        results_dir=tmp_path / "results",
        job_name="job-2",
    )

    env = FakeJob.created_configs[0].agents[0].env
    assert env["EVALUATION_RUN_ID"] == "eval-run-2"
    assert env["SANDBOX_PROXY_URL"] == "https://proxy.example"
    assert "RIDGES_INFERENCE_PROVIDER" not in env
    assert "RIDGES_INFERENCE_API_KEY" not in env


@pytest.mark.anyio
async def test_run_local_task_verifies_digest_when_requested(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    async def fake_prune() -> None:
        return None

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)
    digest_calls: list[Path] = []

    def fake_compute_task_digest(task_dir: Path) -> str:
        digest_calls.append(task_dir)
        return "sha256:expected"

    monkeypatch.setattr(local_harbor_module, "compute_task_digest", fake_compute_task_digest)

    task_dir = tmp_path / "task"
    _write_harbor_task(task_dir)
    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    await local_harbor_module.run_local_task(
        task_dir,
        agent_path=agent_path,
        inference=_inference(),
        task_digest="sha256:expected",
        results_dir=tmp_path / "results",
    )

    assert digest_calls == [task_dir]


@pytest.mark.anyio
async def test_run_local_task_extracts_archive_and_ignores_macos_junk(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    async def fake_prune() -> None:
        return None

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)

    source_dir = tmp_path / "wrapped-task"
    _write_harbor_task(source_dir)
    macosx_dir = tmp_path / "__MACOSX"
    macosx_dir.mkdir()
    (macosx_dir / "junk.txt").write_text("junk\n")
    ds_store = tmp_path / ".DS_Store"
    ds_store.write_text("junk\n")
    archive_path = tmp_path / "wrapped-task.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)
        archive.add(macosx_dir, arcname="__MACOSX")
        archive.add(ds_store, arcname=".DS_Store")

    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    summary = await local_harbor_module.run_local_task(
        archive_path,
        agent_path=agent_path,
        inference=_inference(),
        results_dir=tmp_path / "results",
    )

    assert summary.task_dir.exists()
    assert summary.task_dir.name == "wrapped-task"
    assert summary.task_dir.parent.parent == (tmp_path / "results" / "_task_staging")
    assert FakeJob.created_configs[0].tasks[0].path == summary.task_dir


@pytest.mark.anyio
async def test_run_local_task_reuses_content_addressed_archive_cache(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    async def fake_prune() -> None:
        return None

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)

    source_dir = tmp_path / "wrapped-task"
    _write_harbor_task(source_dir)
    archive_path = tmp_path / "wrapped-task.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)

    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)
    results_dir = tmp_path / "results"

    first = await local_harbor_module.run_local_task(
        archive_path,
        agent_path=agent_path,
        inference=_inference(),
        results_dir=results_dir,
        job_name="job-1",
    )
    second = await local_harbor_module.run_local_task(
        archive_path,
        agent_path=agent_path,
        inference=_inference(),
        results_dir=results_dir,
        job_name="job-2",
    )

    staging_dirs = [path for path in (results_dir / "_task_staging").iterdir() if path.is_dir()]
    assert len(staging_dirs) == 1
    assert first.task_dir == second.task_dir


def test_prune_task_staging_cache_removes_old_entries(tmp_path: Path) -> None:
    staging_root = local_harbor_module.task_staging_cache_dir(tmp_path / "results")
    old_dir = staging_root / "sha256_old"
    fresh_dir = staging_root / "sha256_fresh"
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)

    old_time = time.time() - 120
    os.utime(old_dir, (old_time, old_time))

    removed = local_harbor_module.prune_task_staging_cache(tmp_path / "results", max_age_seconds=60)

    assert removed == [old_dir]
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_list_task_staging_cache_dirs_filters_by_age(tmp_path: Path) -> None:
    staging_root = local_harbor_module.task_staging_cache_dir(tmp_path / "results")
    old_dir = staging_root / "sha256_old"
    fresh_dir = staging_root / "sha256_fresh"
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)

    old_time = time.time() - 120
    os.utime(old_dir, (old_time, old_time))

    cached = local_harbor_module.list_task_staging_cache_dirs(tmp_path / "results", max_age_seconds=60)

    assert cached == [old_dir]


@pytest.mark.anyio
async def test_run_local_task_accepts_one_level_container_directory(tmp_path: Path, monkeypatch) -> None:
    _install_fake_harbor(monkeypatch)

    async def fake_prune() -> None:
        return None

    monkeypatch.setattr(local_harbor_module, "prune_dangling_images", fake_prune)

    container_dir = tmp_path / "astropy__astropy-7166"
    container_dir.mkdir()
    (container_dir / ".DS_Store").write_text("junk\n")
    harbor_root = container_dir / "d77b81b8aa81fd5867ab4b2a405bccfc499ec7f9fb8cf2194411d1508588a1f0"
    _write_harbor_task(harbor_root)

    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    summary = await local_harbor_module.run_local_task(
        container_dir,
        agent_path=agent_path,
        inference=_inference(),
        results_dir=tmp_path / "results",
    )

    assert summary.task_name == "astropy__astropy-7166"
    assert summary.task_dir == harbor_root
    assert FakeJob.created_configs[0].tasks[0].path == harbor_root


@pytest.mark.anyio
async def test_run_local_task_requires_valid_local_inference_config(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write_harbor_task(task_dir)
    agent_path = tmp_path / "agent.py"
    _write_agent(agent_path)

    with pytest.raises(ValueError, match="api_key"):
        await local_harbor_module.run_local_task(
            task_dir,
            agent_path=agent_path,
            inference=LocalInferenceConfig(provider="openrouter", api_key=" "),
        )
