from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import validator.main as validator_main

SCRIPT = Path(__file__).resolve().parents[2] / "setup" / "update-docker-plugins.sh"


class StopAtIntake(Exception):
    """Raised by the fake platform on the first evaluation request."""


def _fake_startup(monkeypatch, *, environment: str, simulate: bool, events: list[str]) -> None:
    """Drive main() through registration with a fake platform and stop at the first evaluation request."""
    monkeypatch.setattr(validator_main.config, "RIDGES_ENVIRONMENT_TYPE", environment)
    monkeypatch.setattr(validator_main.config, "SIMULATE_EVALUATION_RUNS", simulate)
    monkeypatch.setattr(validator_main.config, "MODE", "validator")
    monkeypatch.setattr(validator_main.config, "CLEANUP_ENABLED", False, raising=False)
    monkeypatch.setattr(
        validator_main.config,
        "VALIDATOR_HOTKEY",
        SimpleNamespace(sign=lambda _: b"sig", ss58_address="hotkey"),
        raising=False,
    )

    async def startup() -> None:
        events.append("startup")

    async def post(path: str, *args, **kwargs):
        if path == "/validator/register-as-validator":
            events.append("register")
            return {
                "session_id": "00000000-0000-0000-0000-000000000001",
                "running_agent_timeout_seconds": 1,
                "running_eval_timeout_seconds": 1,
                "max_evaluation_run_log_size_bytes": 1,
            }
        if path == "/validator/request-evaluation":
            events.append("intake")
            raise StopAtIntake
        raise AssertionError(f"unexpected platform call: {path}")

    async def background_loop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(validator_main, "_run_startup_tasks", startup)
    monkeypatch.setattr(validator_main, "post_ridges_platform", post)
    monkeypatch.setattr(validator_main, "ExecutionEngine", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(validator_main, "start_heartbeat_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(validator_main, "set_weights_loop", background_loop)


@pytest.mark.anyio
@pytest.mark.parametrize("environment,simulate", [("docker", False), ("kubernetes", False), ("docker", True)])
async def test_update_runs_after_registration_and_before_intake(monkeypatch, environment, simulate):
    events: list[str] = []
    _fake_startup(monkeypatch, environment=environment, simulate=simulate, events=events)

    async def update() -> None:
        events.append("update")

    monkeypatch.setattr(validator_main, "_update_docker_plugins", update)

    with pytest.raises(StopAtIntake):
        await validator_main.main()

    expected = (
        ["startup", "register", "update", "intake"]
        if environment == "docker" and not simulate
        else ["startup", "register", "intake"]
    )
    assert events == expected


@pytest.mark.anyio
async def test_script_failure_stops_after_registration_and_before_intake(monkeypatch):
    events: list[str] = []
    _fake_startup(monkeypatch, environment="docker", simulate=False, events=events)
    process = SimpleNamespace(wait=AsyncMock(return_value=7), returncode=7)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(validator_main.asyncio, "create_subprocess_exec", launch)

    with pytest.raises(RuntimeError, match="exit 7"):
        await validator_main.main()

    launch.assert_awaited_once_with("bash", str(SCRIPT), start_new_session=True)
    assert events == ["startup", "register"]  # registration (and its 426 path) ran; no work was requested


@pytest.fixture
def shell_environment(tmp_path):
    """Run the actual shell script with offline Docker/download/checksum commands."""
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    driver = command_dir / "driver"
    driver.write_text(
        f"#!{sys.executable}\n"
        + """
import os
import sys
from pathlib import Path

command = Path(sys.argv[0]).name
args = sys.argv[1:]
config = Path(os.environ['DOCKER_CONFIG'])
if command == 'uname':
    print(os.environ.get('TEST_PLUGIN_OS', 'Linux') if args == ['-s'] else os.environ.get('TEST_PLUGIN_ARCH', 'x86_64'))
elif command == 'curl':
    with Path(os.environ['TEST_PLUGIN_LOG']).open('a') as output:
        output.write(args[-1] + '\\n')
    if os.environ.get('TEST_PLUGIN_FAILURE') == 'download' and 'buildx-' in args[-1]:
        sys.exit(22)
    Path(args[-1].rsplit('/', 1)[-1]).write_text('new')
elif command == 'sha256sum':
    if os.environ.get('TEST_PLUGIN_FAILURE') == 'checksum' and args[-1] == 'checksums.txt':
        sys.exit(1)
elif command == 'docker':
    plugin = config / 'cli-plugins' / ('docker-' + args[0])
    installed = plugin.exists() and plugin.read_text() == 'new'
    if args[0] == 'compose':
        version = '5.5.1' if installed else '2.0.0'
        print(version if '--short' in args else 'Docker Compose version v' + version)
    else:
        print('github.com/docker/buildx v' + ('0.37.0' if installed else '0.1.0'))
"""
    )
    driver.chmod(0o755)
    for command in ("curl", "sha256sum", "docker", "uname"):
        (command_dir / command).symlink_to(driver)
    config = tmp_path / "config"
    plugins = config / "cli-plugins"
    plugins.mkdir(parents=True)
    for name in ("docker-compose", "docker-buildx"):
        (plugins / name).write_text("old")
    (config / "config.json").write_text('{"test": "preserve configuration"}')
    env = {
        **os.environ,
        "PATH": str(command_dir) + os.pathsep + os.environ["PATH"],
        "DOCKER_CONFIG": str(config),
        "TEST_PLUGIN_LOG": str(tmp_path / "downloads.log"),
    }
    return tmp_path, config, plugins, env


def _run_script(root, env):
    return subprocess.run(["bash", str(SCRIPT)], cwd=root, env=env, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("arch,relative_config", [("x86_64", False), ("aarch64", True)])
def test_script_installs_both_plugins_and_skips_next_start(shell_environment, arch, relative_config):
    root, config, plugins, env = shell_environment
    env["TEST_PLUGIN_ARCH"] = arch
    if relative_config:
        env["DOCKER_CONFIG"] = "config"
    result = _run_script(root, env)
    assert result.returncode == 0, result.stderr
    for name in ("docker-compose", "docker-buildx"):
        assert (plugins / name).read_text() == "new"
        assert (plugins / name).stat().st_mode & 0o777 == 0o755
    assert (config / "config.json").read_text() == '{"test": "preserve configuration"}'
    downloads = (root / "downloads.log").read_text()
    assert len(downloads.splitlines()) == 4
    assert ("linux-amd64" if arch == "x86_64" else "linux-arm64") in downloads
    assert not list(plugins.glob(".ridges-update.*"))

    result = _run_script(root, env)
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert (root / "downloads.log").read_text() == downloads


@pytest.mark.parametrize("failure", ["download", "checksum"])
def test_script_does_not_replace_plugins_on_download_or_checksum_failure(shell_environment, failure):
    root, _, plugins, env = shell_environment
    env["TEST_PLUGIN_FAILURE"] = failure
    result = _run_script(root, env)
    assert result.returncode != 0
    assert (plugins / "docker-compose").read_text() == "old"
    assert (plugins / "docker-buildx").read_text() == "old"
    assert not list(plugins.glob(".ridges-update.*"))


def test_script_skips_on_non_linux_without_touching_plugins(shell_environment):
    root, _, plugins, env = shell_environment
    env["TEST_PLUGIN_OS"] = "Darwin"
    result = _run_script(root, env)
    assert result.returncode == 0, result.stderr
    assert "skipping" in result.stdout.lower()
    assert (plugins / "docker-compose").read_text() == "old"
    assert (plugins / "docker-buildx").read_text() == "old"
    assert not (root / "downloads.log").exists()
