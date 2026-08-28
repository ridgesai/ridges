from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths

from ridges_harbor._stdlib_contract import GRADED_PATCH_FILENAME, PATCH_FILENAME
from ridges_harbor.verifier import RidgesVerifier


class FakeEnvironment:
    def __init__(self) -> None:
        self.task_env_config = SimpleNamespace(workdir="/app")
        self.commands: list[dict[str, object]] = []
        self.os = SimpleNamespace(value="linux")

    async def exec(self, command: str, cwd: str | None = None, user: str | int | None = None, **_kwargs):
        self.commands.append({"command": command, "cwd": cwd, "user": user})
        return SimpleNamespace(return_code=0, stdout="", stderr="")


@pytest.mark.anyio
async def test_ridges_verifier_applies_patch_as_root_then_delegates(tmp_path: Path, monkeypatch) -> None:
    environment = FakeEnvironment()
    trial_paths = TrialPaths(trial_dir=tmp_path / "trial")
    trial_paths.trial_dir.mkdir(parents=True)
    (trial_paths.verifier_dir).mkdir(parents=True)
    (trial_paths.verifier_dir / "reward.txt").write_text("1\n")

    verifier = RidgesVerifier(
        task=SimpleNamespace(paths=SimpleNamespace(tests_dir=tmp_path / "tests")),
        trial_paths=trial_paths,
        environment=environment,  # type: ignore[arg-type]
    )
    assert verifier._skip_tests_upload is True

    async def fake_super_verify(self):
        return SimpleNamespace(rewards={"reward": 1.0})

    monkeypatch.setattr(
        "harbor.verifier.verifier.Verifier.verify",
        fake_super_verify,
    )

    result = await verifier.verify()

    patch_path = (EnvironmentPaths.agent_dir / PATCH_FILENAME).as_posix()
    graded_path = (EnvironmentPaths.verifier_dir / GRADED_PATCH_FILENAME).as_posix()
    assert environment.commands[0] == {
        "command": f"git apply --check {patch_path}",
        "cwd": "/app",
        "user": "root",
    }
    assert environment.commands[1] == {
        "command": f"git apply {patch_path}",
        "cwd": "/app",
        "user": "root",
    }
    assert environment.commands[2]["user"] == "root"
    assert graded_path in str(environment.commands[2]["command"])
    assert result.rewards == {"reward": 1.0}


@pytest.mark.anyio
async def test_ridges_verifier_raises_when_apply_check_fails(tmp_path: Path) -> None:
    environment = FakeEnvironment()

    async def failing_exec(command: str, cwd: str | None = None, user: str | int | None = None, **_kwargs):
        environment.commands.append({"command": command, "cwd": cwd, "user": user})
        return SimpleNamespace(return_code=1, stdout="", stderr="patch does not apply")

    environment.exec = failing_exec  # type: ignore[method-assign]

    verifier = RidgesVerifier(
        task=SimpleNamespace(paths=SimpleNamespace(tests_dir=tmp_path / "tests")),
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        environment=environment,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="git apply --check"):
        await verifier.verify()
