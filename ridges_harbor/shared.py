"""Shared Harbor execution primitives used by validator and miner entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harbor.models.trial.result import TrialResult

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent.parent / "harbor_test_agent_results"


@dataclass(slots=True)
class HarborRunSummary:
    """The Harbor fields Ridges keeps after a run finishes."""

    trial_result: "TrialResult"
    task_name: str
    job_dir: Path
    task_dir: Path
    trial_dir: Path
