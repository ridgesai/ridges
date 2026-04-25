"""Agent discovery and static validation helpers for the miner CLI."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

LIKELY_AGENT_PATTERNS = (
    "agent.py",
    "*_agent.py",
    "agents/*.py",
)


@dataclass(frozen=True, slots=True)
class AgentValidation:
    """Validation result for a miner agent file."""

    ok: bool
    message: str


def discover_agent_candidates(*roots: Path, limit: int = 8) -> list[Path]:
    """Find likely agent files in the given roots."""
    discovered: list[Path] = []
    seen: set[Path] = set()

    for root in roots:
        if not root.exists():
            continue
        base = root.resolve()
        for pattern in LIKELY_AGENT_PATTERNS:
            for path in sorted(base.glob(pattern)):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                discovered.append(resolved)
                seen.add(resolved)
                if len(discovered) >= limit:
                    return discovered
    return discovered


def validate_agent_file(path: str | Path) -> AgentValidation:
    """Check that an agent file exists and exposes a top-level agent_main."""

    agent_path = Path(path).expanduser().resolve()
    if not agent_path.exists():
        return AgentValidation(False, f"Agent file not found: {agent_path}")

    if not agent_path.is_file():
        return AgentValidation(False, f"Agent path is not a file: {agent_path}")

    try:
        source = agent_path.read_text(encoding="utf-8")
    except OSError as exception:
        return AgentValidation(False, f"Could not read agent file: {exception}")
    except UnicodeDecodeError:
        return AgentValidation(False, f"Agent file is not valid UTF-8 text: {agent_path}")

    try:
        module = ast.parse(source, filename=str(agent_path))
    except SyntaxError as exception:
        return AgentValidation(False, f"Agent file has invalid syntax: {exception.msg}")

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "agent_main":
            return AgentValidation(True, "ok")

    return AgentValidation(False, "Agent file must define a top-level agent_main(input: dict) function")
