from pathlib import Path

from miners.cli.agent_file import discover_agent_candidates, validate_agent_file


def test_discover_agent_candidates_finds_likely_agent_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "agent.py").write_text("def agent_main(input):\n    return ''\n")
    (workspace / "agents").mkdir()
    (workspace / "agents" / "secondary.py").write_text("def agent_main(input):\n    return ''\n")

    discovered = discover_agent_candidates(workspace)

    assert (workspace / "agent.py").resolve() in discovered
    assert (workspace / "agents" / "secondary.py").resolve() in discovered


def test_validate_agent_file_requires_top_level_agent_main(tmp_path: Path) -> None:
    good = tmp_path / "agent.py"
    good.write_text("def agent_main(input):\n    return ''\n")
    bad = tmp_path / "bad.py"
    bad.write_text("def other():\n    return ''\n")

    assert validate_agent_file(good).ok is True
    assert validate_agent_file(bad).ok is False
