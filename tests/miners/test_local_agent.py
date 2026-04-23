from pathlib import Path
from types import SimpleNamespace

import pytest

from miners.local_agent import (
    LOCAL_RUNTIME_BASELINE_REQUIREMENTS_PATH,
    LOCAL_RUNTIME_BOOTSTRAP_LOG_FILENAME,
    LOCAL_RUNTIME_MINER_PACKAGES,
    LocalMinerAgent,
)
from ridges_harbor.agents import RUNTIME_BOOTSTRAP_PROBE_LOG_FILENAME


@pytest.mark.anyio
async def test_local_miner_agent_bootstraps_common_packages(tmp_path: Path, monkeypatch) -> None:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text("def agent_main(input):\n    return ''\n")
    miner = LocalMinerAgent(logs_dir=tmp_path / "logs", agent_path=str(agent_path))
    calls: list[dict[str, object]] = []

    async def fake_exec_with_log(
        environment,
        *,
        executor,
        command,
        log_filename,
        cancelled_detail,
        error_summary=None,
        error_type=None,
        include_output_body=True,
        cwd=None,
    ):
        calls.append(
            {
                "command": command,
                "log_filename": log_filename,
                "cancelled_detail": cancelled_detail,
                "error_summary": error_summary,
                "error_type": error_type,
            }
        )
        return SimpleNamespace(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(miner, "_exec_with_log", fake_exec_with_log)

    await miner._bootstrap_runtime_dependencies(SimpleNamespace())

    assert [call["log_filename"] for call in calls] == [
        RUNTIME_BOOTSTRAP_PROBE_LOG_FILENAME,
        LOCAL_RUNTIME_BOOTSTRAP_LOG_FILENAME,
    ]
    assert str(calls[0]["command"]).startswith("python3 -c ")
    assert "print(" in str(calls[0]["command"])
    assert "ok" in str(calls[0]["command"])
    assert "python3 -m ensurepip --upgrade" in str(calls[1]["command"])
    assert "python3 -m pip install --no-cache-dir" in str(calls[1]["command"])
    assert "python3 -m pip install --break-system-packages --no-cache-dir" in str(calls[1]["command"])
    for package in LOCAL_RUNTIME_MINER_PACKAGES:
        assert package in str(calls[1]["command"])
    assert calls[1]["error_summary"] == "Failed to install local miner baseline packages"
    assert calls[1]["error_type"] is RuntimeError


def test_local_miner_agent_reads_baseline_packages_from_requirements_file() -> None:
    file_packages = tuple(
        line.strip()
        for line in LOCAL_RUNTIME_BASELINE_REQUIREMENTS_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert file_packages == LOCAL_RUNTIME_MINER_PACKAGES
