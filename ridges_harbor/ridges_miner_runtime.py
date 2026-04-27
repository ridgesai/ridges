"""Small runtime used to execute Ridges miners inside Harbor.

CRITICAL INVARIANTS:
- This script runs inside the Harbor task container.
- It stays stdlib-only so the runtime can bootstrap before third-party imports.
- The failure payload shape must match
  `ridges_harbor.runtime_contract.RidgesRuntimeFailure`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from _stdlib_contract import LOAD_AGENT_PHASE, RUN_AGENT_PHASE

RIDGES_MINER_AGENT_MODULE_NAME = "ridges_miner_agent"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _load_agent_module(agent_path: Path) -> ModuleType:
    """Import the miner module from its on-disk path."""
    spec = importlib.util.spec_from_file_location(RIDGES_MINER_AGENT_MODULE_NAME, agent_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load agent from {agent_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iter_exception_chain(exception: BaseException):
    """Yield each exception in the cause/context chain once, skipping cycles."""
    current: BaseException | None = exception
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or current.__context__


def _http_status_from_exception(exception: BaseException) -> int | None:
    """Return the first HTTP status code exposed anywhere in the exception chain."""
    for current in _iter_exception_chain(exception):
        response = getattr(current, "response", None)
        status_code = getattr(response, "status_code", None)

        if isinstance(status_code, int):
            return status_code

    return None


def _exception_chain(exception: BaseException) -> list[dict[str, str]]:
    """Return a JSON-serializable summary of the exception chain."""
    chain: list[dict[str, str]] = []
    for current in _iter_exception_chain(exception):
        exc_type = type(current)
        chain.append(
            {
                "type": exc_type.__name__,
                "module": exc_type.__module__,
                "message": str(current),
            }
        )
    return chain


def _build_failure_payload(
    *,
    exception: BaseException,
    phase: str,
) -> dict[str, Any]:
    """Capture the failure details the backend needs for classification.

    The shape matches 'ridges_harbor.runtime_contract.RidgesRuntimeFailure'.
    """
    return {
        "phase": phase,
        "traceback": traceback.format_exc(),
        "http_status": _http_status_from_exception(exception),
        "missing_module": (getattr(exception, "name", None) if isinstance(exception, ModuleNotFoundError) else None),
        "exception_chain": _exception_chain(exception),
    }


def main() -> int:
    """Run the miner inside the Harbor task environment."""
    parser = argparse.ArgumentParser(description="Execute a Ridges miner inside a Harbor task environment.")
    parser.add_argument("--agent", required=True, help="Path to the miner agent.py inside the environment")
    parser.add_argument("--instruction", required=True, help="Path to the task instruction inside the environment")
    parser.add_argument("--patch", required=True, help="Path to write the generated patch")
    parser.add_argument("--runtime", required=True, help="Path to write runtime metadata and failure details")
    args = parser.parse_args()

    agent_path = Path(args.agent)
    instruction_path = Path(args.instruction)
    patch_path = Path(args.patch)
    runtime_path = Path(args.runtime)

    phase = LOAD_AGENT_PHASE
    try:
        instruction = instruction_path.read_text()
        agent_module = _load_agent_module(agent_path)
        if not hasattr(agent_module, "agent_main"):
            raise RuntimeError("agent.py must define agent_main(input: dict) -> str")

        phase = RUN_AGENT_PHASE
        patch = agent_module.agent_main({"problem_statement": instruction})

        if not isinstance(patch, str):
            raise RuntimeError("agent_main() returned a non-string value")

        if not patch.strip():
            raise RuntimeError("agent_main() returned an empty patch")

        patch_path.write_text(patch)
        return 0
    except Exception as exception:
        _write_json(
            runtime_path,
            _build_failure_payload(
                exception=exception,
                phase=phase,
            ),
        )
        print(f"[RIDGES_HARBOR] Runtime failed during {phase}: {exception}")
        traceback.print_exc(file=sys.stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
