from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import execution.artifacts as artifacts_module
import miners.cli.commands.run_local as run_local_module
import miners.cli.commands.setup as setup_commands_module
from execution.errors import EvaluationRunException
from miners.cli.commands.miner import miner as miner_command
from miners.cli.config import MinerConfig, save_config
from miners.inference_client import LocalInferenceConfig
from miners.local_harbor import CustomSandboxProxyConfig
from models.evaluation_run import EvaluationRunErrorCode
from models.problem import ProblemTestResultStatus


def _summary() -> SimpleNamespace:
    return SimpleNamespace(
        job_dir=Path("/tmp/job"),
        trial_dir=Path("/tmp/trial"),
        task_dir=Path("/tmp/task"),
    )


def _write_agent(path: Path) -> None:
    path.write_text("def agent_main(input):\n    return ''\n")


@pytest.mark.anyio
async def test_run_local_task_and_report_prints_clean_success_summary(monkeypatch, capsys) -> None:
    async def fake_run_local_task(*args, **kwargs):
        return _summary()

    monkeypatch.setattr(run_local_module, "run_local_task", fake_run_local_task)
    monkeypatch.setattr(
        artifacts_module,
        "result_from_summary",
        lambda _summary: SimpleNamespace(
            verifier_reward=1.0,
            test_results=[
                SimpleNamespace(status=ProblemTestResultStatus.PASS),
                SimpleNamespace(status=ProblemTestResultStatus.FAIL),
                SimpleNamespace(status=ProblemTestResultStatus.SKIP),
            ],
        ),
    )

    exit_code = await run_local_module._run_local_task_and_report(
        "/tmp/task",
        agent_path="/tmp/agent.py",
        inference=LocalInferenceConfig(provider="openrouter", api_key="secret"),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Warning: local mode does not enforce evaluation sandbox restrictions." in captured.out
    assert "SUCCEEDED" in captured.out
    assert "reward: 1.0" in captured.out
    assert "tests: 3 total (1 passed, 1 failed, 1 skipped)" in captured.out
    assert captured.err == ""


@pytest.mark.anyio
async def test_run_local_task_and_report_prints_clean_evaluation_failure(monkeypatch, capsys) -> None:
    async def fake_run_local_task(*args, **kwargs):
        return _summary()

    def fake_result_from_summary(_summary):
        raise EvaluationRunException(
            error_code=EvaluationRunErrorCode.AGENT_INVALID_PATCH,
            error_message="bad patch",
        )

    monkeypatch.setattr(run_local_module, "run_local_task", fake_run_local_task)
    monkeypatch.setattr(artifacts_module, "result_from_summary", fake_result_from_summary)

    exit_code = await run_local_module._run_local_task_and_report(
        "/tmp/task",
        agent_path="/tmp/agent.py",
        inference=LocalInferenceConfig(provider="openrouter", api_key="secret"),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "FAILED: AGENT_INVALID_PATCH" in captured.out
    assert "message: bad patch" in captured.out
    assert captured.err == ""


@pytest.mark.anyio
async def test_run_local_task_and_report_prints_setup_failure_to_stderr(monkeypatch, capsys) -> None:
    async def fake_run_local_task(*args, **kwargs):
        raise RuntimeError("setup blew up")

    monkeypatch.setattr(run_local_module, "run_local_task", fake_run_local_task)

    exit_code = await run_local_module._run_local_task_and_report(
        "/tmp/task",
        agent_path="/tmp/agent.py",
        inference=LocalInferenceConfig(provider="openrouter", api_key="secret"),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Warning: local mode does not enforce evaluation sandbox restrictions." in captured.out
    assert "ERROR: RuntimeError: setup blew up" in captured.err


def test_setup_flow_saves_provider_when_one_is_configured(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "miner.toml"
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)

    monkeypatch.setattr(setup_commands_module, "prompt_workspace", lambda default: tmp_path / "ws")
    monkeypatch.setattr(setup_commands_module, "prompt_validated_agent_path", lambda workspace, default: agent_file)
    monkeypatch.setattr(
        setup_commands_module,
        "configured_provider_statuses",
        lambda workspace: [
            SimpleNamespace(provider="openrouter", label="OpenRouter", detail="configured", configured=True),
        ],
    )
    monkeypatch.setattr(setup_commands_module, "prompt_select_provider", lambda configured, default=None: "openrouter")
    monkeypatch.setattr(setup_commands_module, "render_setup_preview", lambda config: None)
    monkeypatch.setattr(setup_commands_module, "prompt_confirm", lambda message, default=True: True)

    runner = CliRunner()
    result = runner.invoke(miner_command, ["setup", "--config-path", str(config_path)])

    assert result.exit_code == 0, result.output
    from miners.cli.config import load_config

    loaded = load_config(config_path)
    assert loaded.workspace == tmp_path / "ws"
    assert loaded.provider == "openrouter"
    assert loaded.agent_path == agent_file


def test_setup_flow_can_save_custom_provider(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "miner.toml"
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)

    monkeypatch.setattr(setup_commands_module, "prompt_workspace", lambda default: tmp_path / "ws")
    monkeypatch.setattr(setup_commands_module, "prompt_validated_agent_path", lambda workspace, default: agent_file)
    monkeypatch.setattr(
        setup_commands_module,
        "configured_provider_statuses",
        lambda workspace: [
            SimpleNamespace(provider="custom", label="Custom (advanced)", detail="configured", configured=True),
        ],
    )
    monkeypatch.setattr(setup_commands_module, "prompt_select_provider", lambda configured, default=None: "custom")
    monkeypatch.setattr(setup_commands_module, "render_setup_preview", lambda config: None)
    monkeypatch.setattr(setup_commands_module, "prompt_confirm", lambda message, default=True: True)

    runner = CliRunner()
    result = runner.invoke(miner_command, ["setup", "--config-path", str(config_path)])

    assert result.exit_code == 0, result.output
    from miners.cli.config import load_config

    loaded = load_config(config_path)
    assert loaded.provider == "custom"


def test_setup_flow_saves_partial_config_when_no_provider_is_configured(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "miner.toml"
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)
    workspace = tmp_path / "ws"

    monkeypatch.setattr(setup_commands_module, "prompt_workspace", lambda default: workspace)
    monkeypatch.setattr(setup_commands_module, "prompt_validated_agent_path", lambda workspace, default: agent_file)
    monkeypatch.setattr(setup_commands_module, "configured_provider_statuses", lambda workspace: [])
    monkeypatch.setattr(setup_commands_module, "render_setup_preview", lambda config: None)
    monkeypatch.setattr(setup_commands_module, "prompt_confirm", lambda message, default=True: True)

    runner = CliRunner()
    result = runner.invoke(miner_command, ["setup", "--config-path", str(config_path)])

    assert result.exit_code == 0, result.output
    from miners.cli.config import load_config

    loaded = load_config(config_path)
    assert loaded.provider is None
    assert (workspace / ".env.miner").exists()
    assert "Created" in result.output
    assert ".env.miner" in result.output


def test_setup_command_recovers_from_legacy_inference_url_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "miner.toml"
    config_path.write_text("[miner]\nworkspace = '/tmp/ws'\ninference_url = 'http://127.0.0.1:1234'\n")
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)

    monkeypatch.setattr(setup_commands_module, "prompt_workspace", lambda default: tmp_path / "ws")
    monkeypatch.setattr(setup_commands_module, "prompt_validated_agent_path", lambda workspace, default: agent_file)
    monkeypatch.setattr(
        setup_commands_module,
        "configured_provider_statuses",
        lambda workspace: [
            SimpleNamespace(provider="openrouter", label="OpenRouter", detail="configured", configured=True),
        ],
    )
    monkeypatch.setattr(setup_commands_module, "prompt_select_provider", lambda configured, default=None: "openrouter")
    monkeypatch.setattr(setup_commands_module, "render_setup_preview", lambda config: None)
    monkeypatch.setattr(setup_commands_module, "prompt_confirm", lambda message, default=True: True)

    runner = CliRunner()
    result = runner.invoke(miner_command, ["setup", "--config-path", str(config_path)])

    assert result.exit_code == 0, result.output
    from miners.cli.config import load_config

    loaded = load_config(config_path)
    assert loaded.provider == "openrouter"
    assert loaded.agent_path == agent_file


def test_run_local_flow_auto_selects_only_configured_provider(tmp_path: Path, monkeypatch) -> None:
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)
    config_path = tmp_path / "miner.toml"
    save_config(MinerConfig(workspace=tmp_path / "ws", agent_path=agent_file), config_path)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    monkeypatch.setattr(
        run_local_module,
        "provider_statuses",
        lambda workspace: {
            "openrouter": SimpleNamespace(provider="openrouter", configured=True),
            "targon": SimpleNamespace(provider="targon", configured=False),
            "chutes": SimpleNamespace(provider="chutes", configured=False),
        },
    )
    monkeypatch.setattr(
        run_local_module,
        "resolve_inference_config",
        lambda provider, workspace: LocalInferenceConfig(provider="openrouter", api_key="secret"),
    )

    async def fake_run_local_task_and_report(task_path, **kwargs):
        return 0

    monkeypatch.setattr(run_local_module, "_run_local_task_and_report", fake_run_local_task_and_report)

    exit_code = run_local_module.run_local_flow(
        config_path=config_path,
        agent_path=None,
        provider=None,
        workspace=None,
        results_dir=None,
        task_path=task_dir,
        dataset=None,
        problem=None,
        debug=False,
        non_interactive=True,
    )

    assert exit_code == 0
    from miners.cli.config import load_config

    assert load_config(config_path).provider == "openrouter"


def test_run_local_flow_auto_selects_custom_provider(tmp_path: Path, monkeypatch) -> None:
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)
    config_path = tmp_path / "miner.toml"
    save_config(MinerConfig(workspace=tmp_path / "ws", agent_path=agent_file), config_path)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    monkeypatch.setattr(
        run_local_module,
        "provider_statuses",
        lambda workspace: {
            "openrouter": SimpleNamespace(provider="openrouter", configured=False),
            "targon": SimpleNamespace(provider="targon", configured=False),
            "chutes": SimpleNamespace(provider="chutes", configured=False),
            "custom": SimpleNamespace(provider="custom", configured=True),
        },
    )
    monkeypatch.setattr(
        run_local_module,
        "resolve_inference_config",
        lambda provider, workspace: CustomSandboxProxyConfig(sandbox_proxy_url="https://proxy.example"),
    )

    async def fake_run_local_task_and_report(task_path, **kwargs):
        return 0

    monkeypatch.setattr(run_local_module, "_run_local_task_and_report", fake_run_local_task_and_report)

    exit_code = run_local_module.run_local_flow(
        config_path=config_path,
        agent_path=None,
        provider=None,
        workspace=None,
        results_dir=None,
        task_path=task_dir,
        dataset=None,
        problem=None,
        debug=False,
        non_interactive=True,
    )

    assert exit_code == 0
    from miners.cli.config import load_config

    assert load_config(config_path).provider == "custom"


def test_run_local_flow_prompts_when_multiple_providers_are_configured(tmp_path: Path, monkeypatch) -> None:
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)
    config_path = tmp_path / "miner.toml"
    save_config(MinerConfig(workspace=tmp_path / "ws", agent_path=agent_file), config_path)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    configured = [
        SimpleNamespace(provider="openrouter", label="OpenRouter", detail="configured", configured=True),
        SimpleNamespace(provider="targon", label="Targon", detail="configured", configured=True),
    ]
    monkeypatch.setattr(
        run_local_module, "provider_statuses", lambda workspace: {status.provider: status for status in configured}
    )
    monkeypatch.setattr(run_local_module, "prompt_select_provider", lambda statuses, default=None: "targon")
    monkeypatch.setattr(
        run_local_module,
        "resolve_inference_config",
        lambda provider, workspace: LocalInferenceConfig(
            provider="targon", api_key="secret", base_url="https://targon.example/v1"
        ),
    )

    async def fake_run_local_task_and_report(task_path, **kwargs):
        return 0

    monkeypatch.setattr(run_local_module, "_run_local_task_and_report", fake_run_local_task_and_report)

    exit_code = run_local_module.run_local_flow(
        config_path=config_path,
        agent_path=None,
        provider=None,
        workspace=None,
        results_dir=None,
        task_path=task_dir,
        dataset=None,
        problem=None,
        debug=False,
        non_interactive=False,
    )

    assert exit_code == 0
    from miners.cli.config import load_config

    assert load_config(config_path).provider == "targon"


def test_run_local_flow_non_interactive_requires_selected_provider_when_multiple_exist(
    tmp_path: Path, monkeypatch
) -> None:
    agent_file = tmp_path / "agent.py"
    _write_agent(agent_file)
    config_path = tmp_path / "miner.toml"
    save_config(MinerConfig(workspace=tmp_path / "ws", agent_path=agent_file), config_path)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    monkeypatch.setattr(
        run_local_module,
        "provider_statuses",
        lambda workspace: {
            "openrouter": SimpleNamespace(provider="openrouter", configured=True),
            "targon": SimpleNamespace(provider="targon", configured=True),
            "chutes": SimpleNamespace(provider="chutes", configured=False),
        },
    )

    with pytest.raises(run_local_module.click.ClickException, match="Multiple local providers are configured"):
        run_local_module.run_local_flow(
            config_path=config_path,
            agent_path=None,
            provider=None,
            workspace=None,
            results_dir=None,
            task_path=task_dir,
            dataset=None,
            problem=None,
            debug=False,
            non_interactive=True,
        )


def test_config_show_prints_provider_and_env_file(tmp_path: Path) -> None:
    config_path = tmp_path / "miner.toml"
    save_config(
        MinerConfig(
            workspace=tmp_path / "ws",
            agent_path=tmp_path / "agent.py",
            provider="openrouter",
        ),
        config_path,
    )

    runner = CliRunner()
    result = runner.invoke(miner_command, ["config", "show", "--config-path", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "Provider" in result.output
    assert "OpenRouter" in result.output
    assert "Env" in result.output


def test_miner_group_without_subcommand_shows_help() -> None:
    runner = CliRunner()
    result = runner.invoke(miner_command, [])

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    assert "setup" in result.output
    assert "run-local" in result.output
