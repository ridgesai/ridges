"""The one exception type the execution layer raises."""

from __future__ import annotations

from typing import Any

from models.evaluation_run import EvaluationRunErrorCode


class EvaluationRunException(Exception):
    """An evaluation-run failure that already knows its platform error code."""

    def __init__(
        self,
        error_code: EvaluationRunErrorCode,
        error_message: str,
        *,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.extra = extra
