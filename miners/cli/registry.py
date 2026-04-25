"""Sync CLI adapter for Harbor dataset browsing and single-problem download."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    """Minimum info the miner CLI needs to render a dataset picker row."""

    id: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class ProblemInfo:
    """Minimum info the miner CLI needs to render a problem picker row."""

    id: str
    name: str


class RegistryAdapter(Protocol):
    """Narrow sync interface for listing datasets/problems and downloading one task."""

    def list_datasets(self) -> list[DatasetInfo]: ...

    def list_problems(self, dataset_id: str) -> list[ProblemInfo]: ...

    def download_problem(
        self,
        dataset_id: str,
        problem_id: str,
        *,
        dest: Path,
    ) -> Path: ...


def _task_id_key(task_id) -> str:
    """Stable unique key for a Harbor TaskId, used for lookup and download."""
    from harbor.models.task.id import GitTaskId, LocalTaskId, PackageTaskId

    if isinstance(task_id, LocalTaskId):
        return str(task_id.path)
    if isinstance(task_id, GitTaskId):
        return str(task_id.path)
    if isinstance(task_id, PackageTaskId):
        ref = task_id.ref or "latest"
        return f"{task_id.org}/{task_id.name}@{ref}"
    raise TypeError(f"Unknown Harbor TaskId type: {type(task_id).__name__}")


def _task_id_display_name(task_id) -> str:
    key = _task_id_key(task_id)
    tail = key.rsplit("/", 1)[-1]
    return tail or key


def _dataset_ref(name: str, version: str | None) -> str:
    return f"{name}@{version}" if version else name


def _split_dataset_ref(dataset_id: str) -> tuple[str, str | None]:
    if "@" not in dataset_id:
        return dataset_id, None
    name, version = dataset_id.rsplit("@", 1)
    return name, version


def _await(awaitable):
    """Run Harbor async calls from the synchronous Click command layer."""
    if asyncio.iscoroutine(awaitable):
        return asyncio.run(awaitable)
    return awaitable


class HarborRegistryAdapter:
    """CLI-oriented adapter backed by Harbor's Python APIs."""

    def __init__(self, *, registry_client, task_client) -> None:
        self._registry = registry_client
        self._task_client = task_client

    @classmethod
    def build(cls) -> "HarborRegistryAdapter":
        from harbor.job import RegistryClientFactory, TaskClient

        return cls(
            registry_client=RegistryClientFactory.create(),
            task_client=TaskClient(),
        )

    def list_datasets(self) -> list[DatasetInfo]:
        summaries = _await(self._registry.list_datasets())
        return [
            DatasetInfo(
                id=_dataset_ref(summary.name, summary.version),
                label=_dataset_ref(summary.name, summary.version),
                description=summary.description,
            )
            for summary in summaries
        ]

    def _get_dataset_spec(self, dataset_id: str):
        """Fetch the full dataset spec when the Harbor client supports it."""
        get_spec = getattr(self._registry, "_get_dataset_spec", None)
        if get_spec is None:
            return None

        name, version = _split_dataset_ref(dataset_id)
        if version is None:
            metadata = _await(self._registry.get_dataset_metadata(dataset_id))
            version = metadata.version
            name = metadata.name
        if version is None:
            return None
        return _await(get_spec(name, version))

    def list_problems(self, dataset_id: str) -> list[ProblemInfo]:
        spec = self._get_dataset_spec(dataset_id)
        if spec is not None:
            return [ProblemInfo(id=task.name, name=task.name) for task in spec.tasks]

        metadata = _await(self._registry.get_dataset_metadata(dataset_id))
        return [
            ProblemInfo(id=_task_id_key(task_id), name=_task_id_display_name(task_id)) for task_id in metadata.task_ids
        ]

    def download_problem(self, dataset_id: str, problem_id: str, *, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        spec = self._get_dataset_spec(dataset_id)
        if spec is not None:
            matches = [task.to_source_task_id() for task in spec.tasks if task.name == problem_id]
            if len(matches) > 1:
                raise RuntimeError(
                    f"Dataset {dataset_id!r} has multiple tasks named {problem_id!r}; refusing ambiguous download"
                )
            if matches:
                batch = _await(self._task_client.download_tasks(matches, output_dir=dest))
                if not batch.results:
                    raise RuntimeError(f"Harbor returned no tasks for dataset={dataset_id} problem={problem_id}")
                return Path(batch.results[0].path)
            raise RuntimeError(f"No task with id={problem_id!r} in dataset {dataset_id!r}")

        metadata = _await(self._registry.get_dataset_metadata(dataset_id))
        matches = [task_id for task_id in metadata.task_ids if _task_id_key(task_id) == problem_id]
        if not matches:
            raise RuntimeError(f"No task with id={problem_id!r} in dataset {dataset_id!r}")
        batch = _await(self._task_client.download_tasks(matches, output_dir=dest))
        if not batch.results:
            raise RuntimeError(f"Harbor returned no tasks for dataset={dataset_id} problem={problem_id}")
        return Path(batch.results[0].path)
