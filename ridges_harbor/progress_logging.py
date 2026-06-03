from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("execution")

LOG_INTERVAL_SECONDS = 60.0
_INSTALLED = False


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _format_task(task: Any) -> str:
    total = "?" if task.total is None else str(int(task.total))
    completed = str(int(task.completed))
    elapsed = _format_elapsed(task.elapsed or 0.0)
    return f"{task.description} ({completed}/{total}, {elapsed} elapsed)"


def _log_task(task: Any) -> None:
    if task.visible:
        logger.info("Harbor progress: %s", _format_task(task))


def _log_running_tasks(progress: Any) -> None:
    for task in progress._tasks.values():
        if task.visible and task.finished_time is None:
            _log_task(task)


def install_logging_harbor_progress() -> None:
    """Patch ``harbor.job.Progress`` once for the process lifetime."""
    global _INSTALLED
    if _INSTALLED:
        return

    import harbor.job as harbor_job

    original_progress = harbor_job.Progress

    class _LoggingProgress(original_progress):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)
            self._stop = threading.Event()

        def __enter__(self):  # noqa: ANN204
            super().__enter__()
            progress = self

            def heartbeat() -> None:
                while not progress._stop.wait(LOG_INTERVAL_SECONDS):
                    _log_running_tasks(progress)

            threading.Thread(target=heartbeat, daemon=True, name="harbor-progress-log").start()
            return self

        def __exit__(self, *args: Any) -> None:
            self._stop.set()
            return super().__exit__(*args)

        def add_task(self, *args: Any, **kwargs: Any) -> Any:
            task_id = super().add_task(*args, **kwargs)
            _log_task(self._tasks[task_id])
            return task_id

        def update(self, task_id: Any, **kwargs: Any) -> None:
            description = kwargs.get("description")
            super().update(task_id, **kwargs)
            if description is not None:
                _log_task(self._tasks[task_id])

        def advance(self, task_id: Any, advance: float = 1) -> None:
            super().advance(task_id, advance)
            _log_task(self._tasks[task_id])

    harbor_job.Progress = _LoggingProgress
    _INSTALLED = True
