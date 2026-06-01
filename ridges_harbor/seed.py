"""Deterministic seed helpers for Harbor evaluation runs."""

from __future__ import annotations

import hashlib

MAX_INFERENCE_SEED = 2**31 - 1


def problem_seed(problem_name: str) -> int:
    """Return a stable non-negative 31-bit seed for a problem name."""
    digest = hashlib.sha256(problem_name.encode("utf-8")).hexdigest()
    return int(digest, 16) % (MAX_INFERENCE_SEED + 1)
