"""Stdlib-only constants shared across the Ridges Harbor adapter/runtime."""

from __future__ import annotations

RUNTIME_FILENAME = "ridges_miner_runtime.py"
INSTRUCTION_FILENAME = "instruction.md"
PATCH_FILENAME = "patch.diff"
RUNTIME_PAYLOAD_FILENAME = "ridges_runtime.json"
RUNTIME_LOG_FILENAME = "runtime.log"

SETUP_LOG_FILENAME = "setup.log"
RUN_LOG_FILENAME = "run.log"
PATCH_CHECK_LOG_FILENAME = "git-apply-check.log"
PATCH_APPLY_LOG_FILENAME = "git-apply.log"
HARBOR_RUNNER_ERROR_FILENAME = "harbor_runner_error.txt"

AGENT_LOG_FILENAMES = (
    SETUP_LOG_FILENAME,
    RUN_LOG_FILENAME,
    RUNTIME_LOG_FILENAME,
    PATCH_CHECK_LOG_FILENAME,
    PATCH_APPLY_LOG_FILENAME,
    RUNTIME_PAYLOAD_FILENAME,
)

LOAD_AGENT_PHASE = "load_agent"
RUN_AGENT_PHASE = "run_agent"
