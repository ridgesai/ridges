"""Shared Harbor execution primitives used by validator and miner entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from harbor.models.trial.result import TrialResult

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent.parent / "harbor_test_agent_results"
DEFAULT_INFERENCE_UPSTREAM_URL = "http://127.0.0.1:9"
DEFAULT_INFERENCE_UPSTREAM_HOST = "127.0.0.1"


@dataclass(slots=True)
class HarborRunSummary:
    """The Harbor fields Ridges keeps after a run finishes."""

    trial_result: "TrialResult"
    task_name: str
    job_dir: Path
    task_dir: Path
    trial_dir: Path


def resolve_inference_gateway(inference_url: str | None) -> tuple[str, str]:
    """Normalize the gateway base URL and return the URL plus the host name."""
    if not inference_url:
        return DEFAULT_INFERENCE_UPSTREAM_URL, DEFAULT_INFERENCE_UPSTREAM_HOST

    stripped_url = inference_url.strip().rstrip("/")
    parsed = urlparse(stripped_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Inference URL must include a scheme and host: {inference_url}")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"Inference URL must be a base URL without path/query/fragment: {inference_url}")
    if not parsed.hostname:
        raise ValueError(f"Failed to parse inference host from URL: {inference_url}")

    return stripped_url, parsed.hostname
