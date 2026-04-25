from pathlib import Path
from uuid import uuid4

import pytest

import execution.engine as engine_module
from execution.engine import ExecutionEngine
from execution.errors import EvaluationRunException
from execution.types import TrialSnapshot
from models.evaluation_run import EvaluationRunErrorCode
from models.harbor_task import HarborRemoteTaskExecutionSpec
from ridges_harbor._stdlib_contract import HARBOR_RUNNER_ERROR_FILENAME, SETUP_LOG_FILENAME

from .helpers import make_summary, successful_verifier_result, valid_execution_spec, write


async def _append_marker(target: list[str], marker: str) -> None:
    target.append(marker)


async def _append_snapshot(target: list[TrialSnapshot], snapshot: TrialSnapshot) -> None:
    target.append(snapshot)


async def _failing_callback() -> None:
    raise RuntimeError("agent callback boom")


async def _failing_snapshot_callback(snapshot: TrialSnapshot) -> None:
    del snapshot
    raise RuntimeError("verification callback boom")


@pytest.mark.anyio
async def test_malformed_verifier_json_becomes_validator_internal_error(tmp_path: Path, monkeypatch) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        test_results=None,
        verifier_result=successful_verifier_result(),
    )
    write(summary.trial_dir / "verifier" / "test_results.json", "{not-json")

    async def fake_run_task(*args, **kwargs):
        return summary

    monkeypatch.setattr(engine_module, "run_task", fake_run_task)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")
    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)

    engine = ExecutionEngine("http://inference")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "malformed JSON artifact" in exc_info.value.error_message
    assert "Expecting property name enclosed in double quotes" in exc_info.value.error_message


@pytest.mark.anyio
async def test_harbor_local_task_kind_is_rejected_with_clear_error(tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    engine = ExecutionEngine("http://inference")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec={
                "kind": "harbor_local_task",
                "task_name": "update-status-file",
                "task_digest": "sha256:fake",
            },
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "Only promoted remote Harbor tasks are supported" in exc_info.value.error_message


@pytest.mark.anyio
async def test_malformed_remote_execution_spec_is_wrapped_as_validator_internal_error(tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    engine = ExecutionEngine("http://inference")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec={
                "kind": "harbor_remote_task",
                "task_name": "update-status-file",
            },
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "Invalid Harbor execution spec for 'update-status-file'" in exc_info.value.error_message
    assert "dataset_name" in exc_info.value.error_message
    assert "task_digest" in exc_info.value.error_message


@pytest.mark.anyio
async def test_remote_task_cache_lookup_uses_execution_spec_task_name(tmp_path: Path, monkeypatch) -> None:
    expected_task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    captured: dict[str, str] = {}

    def fake_get_cached_task(task_name: str, task_digest: str):
        captured["task_name"] = task_name
        captured["task_digest"] = task_digest
        return expected_task_dir

    async def fake_get_or_download_task(*args, **kwargs):
        raise AssertionError("download should not be used on cache hit")

    monkeypatch.setattr(engine_module, "get_cached_task", fake_get_cached_task)
    monkeypatch.setattr(engine_module, "get_or_download_task", fake_get_or_download_task)

    resolved = await ExecutionEngine("http://inference")._resolve_task_dir(
        HarborRemoteTaskExecutionSpec(
            kind="harbor_remote_task",
            dataset_name="test_dataset",
            task_name="update-status-file",
            s3_key="tasks/test_dataset/update-status-file.tar.gz",
            task_digest="sha256:fake",
        ),
        problem_name="update-status-file",
        fetch_task_url=None,
    )

    assert resolved == expected_task_dir
    assert captured == {
        "task_name": "update-status-file",
        "task_digest": "sha256:fake",
    }


@pytest.mark.anyio
async def test_evaluate_attaches_job_dir_from_resolved_request(tmp_path: Path, monkeypatch) -> None:
    summary = make_summary(
        tmp_path,
        patch=None,
        test_results=None,
        verifier_result=successful_verifier_result(),
    )

    async def fake_run_task(*args, **kwargs):
        return summary

    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)

    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")
    evaluation_run_id = uuid4()

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=evaluation_run_id,
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert exc_info.value.extra["job_dir"] == summary.job_dir


@pytest.mark.anyio
async def test_evaluate_orchestrates_run_task_with_stable_request(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")
    captured: dict[str, object] = {}

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(task_dir_arg, **kwargs):
        captured["task_dir"] = task_dir_arg
        captured.update(kwargs)
        return make_summary(
            tmp_path,
            test_results=None,
            verifier_result=successful_verifier_result(),
        )

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")
    evaluation_run_id = uuid4()

    result = await engine.evaluate(
        evaluation_run_id=evaluation_run_id,
        problem_name="update-status-file",
        execution_spec=valid_execution_spec(),
        agent_path=agent_path,
        agent_code=None,
    )

    assert result.backend == "harbor"
    assert captured["task_dir"] == task_dir
    assert captured["task_name"] == "update-status-file"
    assert captured["task_digest"] == "sha256:fake"
    assert captured["evaluation_run_id"] == str(evaluation_run_id)
    assert captured["results_dir"] == (tmp_path / "results").resolve()
    assert captured["job_name"] == f"update-status-file__{evaluation_run_id}"


@pytest.mark.anyio
async def test_evaluate_translates_harbor_hooks_into_domain_callbacks(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")
    snapshot_calls: list[TrialSnapshot] = []
    agent_started_calls: list[str] = []

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        trial_dir = tmp_path / "trial"
        write(trial_dir / "agent" / "patch.diff", "PATCH")
        write(trial_dir / "agent" / SETUP_LOG_FILENAME, "setup ok")

        agent_event = type("AgentEvent", (), {})()
        await kwargs["on_agent_started"](agent_event)

        verification_event = type(
            "VerificationEvent",
            (),
            {
                "trial_id": "trial-1",
                "config": type("Config", (), {"trials_dir": tmp_path})(),
            },
        )()
        write(tmp_path / "trial-1" / "agent" / "patch.diff", "PATCH")
        write(tmp_path / "trial-1" / "agent" / SETUP_LOG_FILENAME, "setup ok")
        await kwargs["on_verification_started"](verification_event)

        return make_summary(
            tmp_path,
            test_results=None,
            verifier_result=successful_verifier_result(),
        )

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    result = await engine.evaluate(
        evaluation_run_id=uuid4(),
        problem_name="update-status-file",
        execution_spec=valid_execution_spec(),
        agent_path=agent_path,
        agent_code=None,
        on_agent_started=lambda: _append_marker(agent_started_calls, "started"),
        on_verification_started=lambda snapshot: _append_snapshot(snapshot_calls, snapshot),
    )

    assert result.backend == "harbor"
    assert agent_started_calls == ["started"]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0].patch == "PATCH"
    assert "# setup.log\nsetup ok" in snapshot_calls[0].agent_logs


@pytest.mark.anyio
async def test_evaluate_swallows_domain_callback_failures(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        agent_event = type("AgentEvent", (), {})()
        await kwargs["on_agent_started"](agent_event)

        verification_event = type(
            "VerificationEvent",
            (),
            {
                "trial_id": "trial-1",
                "config": type("Config", (), {"trials_dir": tmp_path})(),
            },
        )()
        write(tmp_path / "trial-1" / "agent" / "patch.diff", "PATCH")
        write(tmp_path / "trial-1" / "agent" / SETUP_LOG_FILENAME, "setup ok")
        await kwargs["on_verification_started"](verification_event)

        return make_summary(
            tmp_path,
            test_results=None,
            verifier_result=successful_verifier_result(),
        )

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    result = await engine.evaluate(
        evaluation_run_id=uuid4(),
        problem_name="update-status-file",
        execution_spec=valid_execution_spec(),
        agent_path=agent_path,
        agent_code=None,
        on_agent_started=_failing_callback,
        on_verification_started=_failing_snapshot_callback,
    )

    assert result.backend == "harbor"


@pytest.mark.anyio
async def test_digest_mismatch_from_run_task_becomes_validator_internal_error(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        raise RuntimeError("Harbor task digest mismatch for update-status-file: expected sha256:fake, got sha256:bad")

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "Harbor task digest mismatch" in exc_info.value.error_message


@pytest.mark.anyio
async def test_catch_all_preserves_job_dir_when_no_logs_are_available(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        job_dir = Path(kwargs["results_dir"]) / str(kwargs["job_name"])
        job_dir.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("unexpected crash without logs")

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")
    evaluation_run_id = uuid4()

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=evaluation_run_id,
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert exc_info.value.extra == {
        "job_dir": (tmp_path / "results" / f"update-status-file__{evaluation_run_id}").resolve()
    }


@pytest.mark.anyio
async def test_catch_all_attaches_job_log_context(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        job_dir = Path(kwargs["results_dir"]) / str(kwargs["job_name"])
        write(job_dir / "job.log", "job-level failure details")
        raise RuntimeError("unexpected crash with job log")

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "job-level failure details" in exc_info.value.extra["agent_logs"]


@pytest.mark.anyio
async def test_catch_all_attaches_single_trial_context(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        job_dir = Path(kwargs["results_dir"]) / str(kwargs["job_name"])
        trial_dir = job_dir / "update-status-file__trial-1"
        write(job_dir / "job.log", "job-level failure details")
        write(trial_dir / "trial.log", "trial-level failure details")
        write(trial_dir / "exception.txt", "traceback details")
        write(trial_dir / "agent" / SETUP_LOG_FILENAME, "setup ok")
        write(trial_dir / "verifier" / "test-stdout.txt", "verifier output")
        raise RuntimeError("unexpected crash with single trial logs")

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "job-level failure details" in exc_info.value.extra["agent_logs"]
    assert "# trial.log\ntrial-level failure details" in exc_info.value.extra["agent_logs"]
    assert f"# {SETUP_LOG_FILENAME}\nsetup ok" in exc_info.value.extra["agent_logs"]
    assert exc_info.value.extra["eval_logs"] == "verifier output"


@pytest.mark.anyio
async def test_catch_all_never_raises_when_one_expected_log_path_is_a_directory(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "cache" / "sha256_fake" / "update-status-file"
    task_dir.mkdir(parents=True)
    agent_path = tmp_path / "agent.py"
    write(agent_path, "def agent_main(_input):\n    return ''\n")
    original_error = RuntimeError("unexpected crash with broken log path")

    async def fake_resolve_task_dir(self, execution_spec, problem_name, fetch_task_url):
        return task_dir

    async def fake_run_task(*args, **kwargs):
        job_dir = Path(kwargs["results_dir"]) / str(kwargs["job_name"])
        write(job_dir / "job.log", "job-level failure details")
        (job_dir / HARBOR_RUNNER_ERROR_FILENAME).mkdir(parents=True, exist_ok=True)
        raise original_error

    monkeypatch.setattr(ExecutionEngine, "_resolve_task_dir", fake_resolve_task_dir)
    monkeypatch.setattr(engine_module, "run_task", fake_run_task)

    engine = ExecutionEngine("http://inference", harbor_results_dir=tmp_path / "results")

    with pytest.raises(EvaluationRunException) as exc_info:
        await engine.evaluate(
            evaluation_run_id=uuid4(),
            problem_name="update-status-file",
            execution_spec=valid_execution_spec(),
            agent_path=agent_path,
            agent_code=None,
        )

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert exc_info.value.__cause__ is original_error
    assert "job-level failure details" in exc_info.value.extra["agent_logs"]
