import os
from pathlib import Path

import pytest
from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.models.task.config import TaskOS
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from harbor.verifier.verifier import Verifier

from ridges_harbor.verifier import RidgesVerifier

SEPARATE_VERIFIER_TASK_TOML = '[verifier]\nenvironment_mode = "separate"\n'
SHARED_VERIFIER_TASK_TOML = '[verifier]\nenvironment_mode = "shared"\n'

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root can read a mode-000 file, so the host-side permission failure cannot be reproduced"
)


class StubEnvironment:
    """Just enough of a mounted Harbor environment for Verifier.verify() to run."""

    os = TaskOS.LINUX
    capabilities = EnvironmentCapabilities(mounted=True)

    def __init__(self, on_prepare_logs=None, exec_error: Exception | None = None) -> None:
        self.exec_commands: list[str] = []
        self.uploaded_dirs: list[tuple[str, str]] = []
        self.prepare_logs_calls = 0
        self._on_prepare_logs = on_prepare_logs
        self._exec_error = exec_error

    async def exec(self, command: str, env=None, **kwargs) -> None:
        self.exec_commands.append(command)
        if self._exec_error is not None:
            raise self._exec_error

    async def upload_dir(self, source_dir, target_dir: str) -> None:
        self.uploaded_dirs.append((str(source_dir), target_dir))

    async def prepare_logs_for_host(self) -> None:
        self.prepare_logs_calls += 1
        if self._on_prepare_logs is not None:
            self._on_prepare_logs()


def _make_task(tmp_path: Path, task_toml: str) -> Task:
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.toml").write_text(task_toml)
    (task_dir / "instruction.md").write_text("Make the hidden tests pass.\n")
    (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\necho 1 > /logs/verifier/reward.txt\n")
    return Task(task_dir)


def _make_trial_paths(tmp_path: Path) -> TrialPaths:
    trial_paths = TrialPaths(trial_dir=tmp_path / "trial")
    trial_paths.mkdir()
    trial_paths.reward_text_path.write_text("1\n")
    return trial_paths


@pytest.mark.anyio
@needs_non_root
async def test_verify_fixes_ownership_and_rereads_unreadable_reward(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    reward_path = trial_paths.reward_text_path
    # What a root-owned, mode-0600 reward.txt looks like to a non-root validator.
    reward_path.chmod(0o000)
    environment = StubEnvironment(on_prepare_logs=lambda: reward_path.chmod(0o600))

    result = await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert result.rewards == {"reward": 1.0}
    assert environment.prepare_logs_calls == 1


@pytest.mark.anyio
async def test_verify_leaves_ownership_alone_when_reward_is_readable(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    environment = StubEnvironment()

    result = await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert result.rewards == {"reward": 1.0}
    assert environment.prepare_logs_calls == 0


@pytest.mark.anyio
@needs_non_root
async def test_verify_reraises_when_ownership_fix_does_not_help(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    trial_paths.reward_text_path.chmod(0o000)
    environment = StubEnvironment()

    with pytest.raises(PermissionError):
        await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert environment.prepare_logs_calls == 1


@pytest.mark.anyio
async def test_separate_verifier_mode_keeps_tests_baked_into_image(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    environment = StubEnvironment()

    await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert environment.uploaded_dirs == []


@pytest.mark.anyio
async def test_shared_verifier_mode_uploads_tests_like_harbor(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SHARED_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    environment = StubEnvironment()

    await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert environment.uploaded_dirs == [(str(task.paths.tests_dir), "/tests")]


@pytest.mark.anyio
async def test_verify_propagates_permission_error_from_running_tests_even_with_reward_present(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)  # a readable reward.txt already exists
    environment = StubEnvironment(exec_error=PermissionError(13, "Permission denied", "/var/run/docker.sock"))

    with pytest.raises(PermissionError, match="docker.sock"):
        await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert environment.prepare_logs_calls == 0


@pytest.mark.anyio
async def test_verify_does_not_mask_permission_error_when_no_reward_exists(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = TrialPaths(trial_dir=tmp_path / "trial")
    trial_paths.mkdir()  # no reward file: the tests never ran
    environment = StubEnvironment(exec_error=PermissionError(13, "Permission denied", "/var/run/docker.sock"))

    with pytest.raises(PermissionError, match="docker.sock"):
        await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert environment.prepare_logs_calls == 0


@pytest.mark.anyio
@needs_non_root
async def test_verify_recovers_a_zero_reward(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    reward_path = trial_paths.reward_text_path
    reward_path.write_text("0\n")
    reward_path.chmod(0o000)
    environment = StubEnvironment(on_prepare_logs=lambda: reward_path.chmod(0o600))

    result = await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert result.rewards == {"reward": 0.0}
    assert environment.prepare_logs_calls == 1


@pytest.mark.anyio
@needs_non_root
async def test_verify_recovers_json_reward_and_keeps_json_precedence(tmp_path: Path) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)  # readable reward.txt says 1
    json_path = trial_paths.reward_json_path
    json_path.write_text('{"reward": 0.25}\n')
    json_path.chmod(0o000)
    environment = StubEnvironment(on_prepare_logs=lambda: json_path.chmod(0o600))

    result = await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert result.rewards == {"reward": 0.25}
    assert environment.prepare_logs_calls == 1


@pytest.mark.anyio
async def test_verify_recovers_when_parser_fails_until_ownership_is_fixed(tmp_path: Path, monkeypatch) -> None:
    """Root-safe variant: the permission failure is injected at the parser instead of the filesystem."""
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    environment = StubEnvironment()
    original_parse = Verifier._parse_reward_text

    def parse_unless_ownership_fixed(self):
        if environment.prepare_logs_calls == 0:
            raise PermissionError(13, "Permission denied", str(trial_paths.reward_text_path))
        return original_parse(self)

    monkeypatch.setattr(Verifier, "_parse_reward_text", parse_unless_ownership_fixed)

    result = await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert result.rewards == {"reward": 1.0}
    assert environment.prepare_logs_calls == 1


@pytest.mark.anyio
async def test_verify_propagates_original_error_when_recovery_fails(tmp_path: Path, monkeypatch) -> None:
    task = _make_task(tmp_path, SEPARATE_VERIFIER_TASK_TOML)
    trial_paths = _make_trial_paths(tmp_path)
    environment = StubEnvironment()

    def always_denied(self):
        raise PermissionError(13, "Permission denied", str(trial_paths.reward_text_path))

    monkeypatch.setattr(Verifier, "_parse_reward_text", always_denied)

    with pytest.raises(PermissionError, match="reward.txt") as excinfo:
        await RidgesVerifier(task=task, trial_paths=trial_paths, environment=environment).verify()

    assert type(excinfo.value) is PermissionError  # the raw second failure, not the private wrapper
    assert environment.prepare_logs_calls == 1
