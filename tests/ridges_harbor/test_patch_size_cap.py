"""An oversized miner patch is rejected and deleted inside the agent container."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.agent.context import AgentContext

import ridges_harbor.agents as agents_module
from ridges_harbor.agents import RidgesMinerAgent
from ridges_harbor.runtime_contract import MinerInvalidPatchError


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A clean task working directory at its baseline commit."""
    repo = tmp_path / "workdir"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "status.txt").write_text("pending\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


@pytest.fixture
def valid_patch(workdir: Path) -> str:
    """A patch that applies to the baseline, with the working directory restored."""
    (workdir / "status.txt").write_text("done\n")
    patch = _git(workdir, "diff")
    _git(workdir, "checkout", "--", ".")
    return patch


@pytest.fixture
def miner(tmp_path: Path, workdir: Path, monkeypatch) -> RidgesMinerAgent:
    """A wrapper whose raw patch lives under tmp_path instead of /logs/agent."""
    monkeypatch.setattr(
        RidgesMinerAgent, "_env_raw_patch_path", property(lambda self: str(tmp_path / "raw-patch.diff"))
    )
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    return RidgesMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path), workdir=str(workdir))


def _run_check(miner: RidgesMinerAgent) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", miner._patch_check_command()], cwd=miner.workdir, capture_output=True, text=True
    )


def test_patch_over_the_cap_is_rejected_and_deleted(miner: RidgesMinerAgent, valid_patch: str, monkeypatch) -> None:
    """Left in place, the file would still be copied to the validator when the run fails."""
    raw_patch = Path(miner._env_raw_patch_path)
    raw_patch.write_text(valid_patch)
    monkeypatch.setattr(agents_module, "MAX_PATCH_BYTES", len(valid_patch.encode()) - 1)

    completed = _run_check(miner)

    assert completed.returncode != 0
    assert not raw_patch.exists()
    assert f"{len(valid_patch.encode())} bytes" in completed.stderr
    assert f"{len(valid_patch.encode()) - 1} bytes" in completed.stderr


def test_patch_exactly_at_the_cap_is_still_checked_and_kept(
    miner: RidgesMinerAgent, valid_patch: str, monkeypatch
) -> None:
    raw_patch = Path(miner._env_raw_patch_path)
    raw_patch.write_text(valid_patch)
    monkeypatch.setattr(agents_module, "MAX_PATCH_BYTES", len(valid_patch.encode()))

    completed = _run_check(miner)

    assert completed.returncode == 0, completed.stderr
    assert raw_patch.read_text() == valid_patch


def test_small_patch_that_does_not_apply_is_rejected_but_kept(miner: RidgesMinerAgent) -> None:
    """The size cap must not change how an ordinary invalid patch is handled."""
    raw_patch = Path(miner._env_raw_patch_path)
    raw_patch.write_text("this is not a patch\n")

    completed = _run_check(miner)

    assert completed.returncode != 0
    assert raw_patch.exists()


def test_default_cap_admits_an_ordinary_patch(miner: RidgesMinerAgent, valid_patch: str) -> None:
    Path(miner._env_raw_patch_path).write_text(valid_patch)

    assert _run_check(miner).returncode == 0


@pytest.mark.anyio
async def test_run_fails_as_invalid_patch_and_never_publishes_an_oversized_patch(
    miner: RidgesMinerAgent, valid_patch: str, monkeypatch
) -> None:
    raw_patch = Path(miner._env_raw_patch_path)
    monkeypatch.setattr(agents_module, "MAX_PATCH_BYTES", len(valid_patch.encode()) - 1)
    executed: list[str] = []

    class Environment:
        """Really runs the patch check; every other step is assumed to succeed."""

        async def upload_file(self, source: Path, destination: str) -> None:
            return None

        async def exec(self, command: str, user=None, env=None, cwd=None, timeout_sec=None):
            executed.append(command)
            if "ridges_miner_runtime.py" in command and "--patch" in command:
                raw_patch.write_text(valid_patch)  # the miner returns its patch
            if "git apply --check" not in command:
                return SimpleNamespace(return_code=0, stdout="", stderr="")
            completed = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True, text=True)
            return SimpleNamespace(return_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)

    context = AgentContext()
    with pytest.raises(MinerInvalidPatchError, match="bytes"):
        await miner.run("fix the task", environment=Environment(), context=context)

    assert not raw_patch.exists()
    assert not context.metadata
    assert not any(miner._env_patch_path in command for command in executed)
