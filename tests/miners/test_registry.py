from pathlib import Path

import pytest

from miners.cli.registry import DatasetInfo, HarborRegistryAdapter, ProblemInfo


def test_harbor_adapter_preserves_dataset_versions_and_problem_names(tmp_path: Path) -> None:
    from harbor.models.registry import DatasetSpec, DatasetSummary, RegistryTaskId
    from harbor.tasks.client import BatchDownloadResult, TaskDownloadResult

    class FakeRegistryClient:
        def list_datasets(self):
            return [
                DatasetSummary(name="polyglot", version="1.0", description="demo", task_count=2),
            ]

        def _get_dataset_spec(self, name: str, version: str):
            assert name == "polyglot"
            assert version == "1.0"
            return DatasetSpec(
                name=name,
                version=version,
                description="demo",
                tasks=[
                    RegistryTaskId(name="astropy__astropy-7166", path=Path("tasks/astropy__astropy-7166")),
                    RegistryTaskId(name="sympy__sympy-12419", path=Path("tasks/sympy__sympy-12419")),
                ],
            )

    class FakeTaskClient:
        def __init__(self) -> None:
            self.download_calls: list[tuple[list, Path]] = []

        def download_tasks(self, task_ids, *, output_dir, **_kwargs):
            self.download_calls.append((list(task_ids), output_dir))
            out = output_dir / "astropy__astropy-7166"
            out.mkdir(parents=True, exist_ok=True)
            return BatchDownloadResult(
                results=[TaskDownloadResult(path=out, download_time_sec=0.0, cached=False)],
                total_time_sec=0.0,
            )

    fake_task_client = FakeTaskClient()
    adapter = HarborRegistryAdapter(registry_client=FakeRegistryClient(), task_client=fake_task_client)

    assert adapter.list_datasets() == [DatasetInfo(id="polyglot@1.0", label="polyglot@1.0", description="demo")]
    assert adapter.list_problems("polyglot@1.0") == [
        ProblemInfo(id="astropy__astropy-7166", name="astropy__astropy-7166"),
        ProblemInfo(id="sympy__sympy-12419", name="sympy__sympy-12419"),
    ]

    resolved = adapter.download_problem("polyglot@1.0", "astropy__astropy-7166", dest=tmp_path)
    assert resolved == tmp_path / "astropy__astropy-7166"
    sent_ids, sent_dir = fake_task_client.download_calls[0]
    assert len(sent_ids) == 1
    assert sent_ids[0].path == Path("tasks/astropy__astropy-7166")
    assert sent_dir == tmp_path


def test_harbor_adapter_raises_when_named_problem_is_missing(tmp_path: Path) -> None:
    from harbor.models.registry import DatasetSpec, RegistryTaskId

    class FakeRegistryClient:
        def _get_dataset_spec(self, name: str, version: str):
            return DatasetSpec(
                name=name,
                version=version,
                description="",
                tasks=[RegistryTaskId(name="real-problem", path=Path("tasks/real-problem"))],
            )

    adapter = HarborRegistryAdapter(registry_client=FakeRegistryClient(), task_client=None)

    try:
        adapter.download_problem("polyglot@1.0", "missing-problem", dest=tmp_path)
    except RuntimeError as exc:
        assert "missing-problem" in str(exc)
        assert "polyglot@1.0" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_harbor_adapter_raises_when_named_problem_is_ambiguous(tmp_path: Path) -> None:
    from harbor.models.registry import DatasetSpec, RegistryTaskId

    class FakeRegistryClient:
        def _get_dataset_spec(self, name: str, version: str):
            return DatasetSpec(
                name=name,
                version=version,
                description="",
                tasks=[
                    RegistryTaskId(name="dup-problem", path=Path("tasks/dup-1")),
                    RegistryTaskId(name="dup-problem", path=Path("tasks/dup-2")),
                ],
            )

    adapter = HarborRegistryAdapter(registry_client=FakeRegistryClient(), task_client=None)

    with pytest.raises(RuntimeError, match="multiple tasks named 'dup-problem'"):
        adapter.download_problem("polyglot@1.0", "dup-problem", dest=tmp_path)
