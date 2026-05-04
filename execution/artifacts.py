"""
Translate what Harbor returns into a Ridges ExecutionResult.

Entry point is 'result_from_summary'.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harbor.models.trial.paths import TrialPaths
from pydantic import BaseModel, Field, ValidationError

from execution.errors import EvaluationRunException
from execution.failure_classifier import (
    InvalidRuntimePayloadError,
    classify_trial_failure,
    extract_runtime_failure,
)
from execution.types import ExecutionResult, FailureContext, TrialSnapshot
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus
from ridges_harbor._stdlib_contract import AGENT_LOG_FILENAMES, HARBOR_RUNNER_ERROR_FILENAME
from ridges_harbor.runner import HarborRunSummary


class VerifierTestResultsPayload(BaseModel):
    """Schema validation for test_results.json."""

    success: bool | None = None
    output: list[ProblemTestResult] = Field(default_factory=list)
    error: str | None = None


def result_from_summary(summary: HarborRunSummary) -> ExecutionResult:
    """Read Harbor artifacts and decide whether the run succeeded or failed.

    Raises EvaluationRunException on failure, with the error classified and
    logs attached.
    """
    trial_paths = TrialPaths(trial_dir=summary.trial_dir)
    context = collect_execution_logs(summary, trial_paths=trial_paths)

    try:
        runtime_failure = extract_runtime_failure(summary=summary)
    except InvalidRuntimePayloadError as exception:
        context.fail_validator(
            f"Harbor produced an invalid ridges_runtime payload: {exception}",
            cause=exception,
        )

    trial_exception = summary.trial_result.exception_info
    if trial_exception is not None:
        failure = classify_trial_failure(
            trial_result=summary.trial_result,
            trial_exception=trial_exception,
            runtime_failure=runtime_failure,
        )

        raise EvaluationRunException(
            error_code=failure.error_code,
            error_message=f"{failure.error_code.get_error_message()}: {failure.detail}",
            extra=context.as_extra(),
        )

    return parse_execution_artifacts(summary, trial_paths=trial_paths, context=context)


# Log collection


def collect_execution_logs(
    summary: HarborRunSummary,
    *,
    trial_paths: TrialPaths,
) -> FailureContext:
    """Collect the log sections needed by both success and failure paths."""
    agent_logs = collect_named_logs(_agent_log_paths(trial_paths))
    return FailureContext(
        agent_logs=agent_logs,
        eval_logs=read_eval_logs(trial_paths=trial_paths),
        job_dir=summary.job_dir,
    )


def read_trial_snapshot(trial_dir: Path) -> TrialSnapshot:
    """Read the patch and surfaced agent logs from a completed agent phase."""
    trial_paths = TrialPaths(trial_dir=trial_dir)
    return TrialSnapshot(
        patch=read_text(trial_paths.agent_dir / "patch.diff"),
        agent_logs=collect_named_logs(_agent_log_paths(trial_paths)),
    )


def collect_job_crash_context(job_dir: Path) -> dict[str, Any]:
    """Collect best-effort crash context for engine-level unexpected failures."""
    try:
        if not job_dir.exists():
            return {}
    except OSError:
        return {}

    extra: dict[str, Any] = {"job_dir": job_dir}
    agent_logs = collect_named_logs(
        [
            job_dir / "job.log",
            job_dir / HARBOR_RUNNER_ERROR_FILENAME,
        ],
        ignore_read_errors=True,
    )

    evaluation_logs = ""
    trial_paths = _resolve_single_trial_paths(job_dir)
    if trial_paths is not None:
        agent_logs = merge_logs(
            agent_logs,
            collect_named_logs(_agent_log_paths(trial_paths), ignore_read_errors=True),
        )
        evaluation_logs = _read_eval_logs_best_effort(trial_paths=trial_paths)

    if agent_logs:
        extra["agent_logs"] = agent_logs

    if evaluation_logs:
        extra["eval_logs"] = evaluation_logs

    return extra


def _agent_log_paths(trial_paths: TrialPaths) -> list[Path]:
    """Return the per-trial log files Ridges surfaces as 'agent_logs'."""
    return [
        *(trial_paths.agent_dir / filename for filename in AGENT_LOG_FILENAMES),
        trial_paths.log_path,
        trial_paths.exception_message_path,
    ]


def _resolve_single_trial_paths(job_dir: Path) -> TrialPaths | None:
    """Return TrialPaths for a job directory containing exactly one trial, else None."""
    try:
        trial_dirs = sorted(path for path in job_dir.iterdir() if path.is_dir())
    except OSError:
        return None

    if len(trial_dirs) != 1:
        return None

    return TrialPaths(trial_dir=trial_dirs[0])


def collect_named_logs(paths: list[Path], *, ignore_read_errors: bool = False) -> str:
    """Concatenate non-empty file contents with '# <filename>' section headers.

    When 'ignore_read_errors' is True, unreadable files are silently skipped.
    """
    chunks: list[str] = []
    for path in paths:
        try:
            content = read_text(path).strip()
        except OSError:
            if ignore_read_errors:
                continue
            raise

        if content:
            chunks.append(f"# {path.name}\n{content}")

    return "\n\n".join(chunks)


def merge_logs(*sections: str) -> str:
    """Join non-empty log sections with a blank line-break."""
    return "\n\n".join(section for section in sections if section)


# Success parsing


def parse_execution_artifacts(
    summary: HarborRunSummary,
    *,
    trial_paths: TrialPaths,
    context: FailureContext,
) -> ExecutionResult:
    """Parse completed verifier outputs from a Harbor trial into an ExecutionResult."""

    reward = extract_reward_value(summary, context=context)

    test_results = parse_structured_test_results(
        trial_paths.verifier_dir / "test_results.json",
        trial_paths.artifacts_dir / "test_results.json",
        trial_paths.verifier_dir / "report.json",
        trial_paths.artifacts_dir / "report.json",
        context=context,
    )

    patch = read_text(trial_paths.agent_dir / "patch.diff")
    if not patch:
        context.fail_validator("Harbor completed without producing a patch artifact")

    return ExecutionResult(
        backend="harbor",
        patch=patch,
        verifier_reward=reward,
        test_results=test_results,
        agent_logs=context.agent_logs,
        eval_logs=context.eval_logs,
        job_dir=context.job_dir,
    )


def extract_reward_value(summary: HarborRunSummary, *, context: FailureContext) -> float:
    """Pull a single numeric reward out of Harbor's verifier result.

    Accepts either '{"reward": N}' or a one-key payload.
    """

    verifier_result = summary.trial_result.verifier_result
    if verifier_result is None:
        context.fail_validator("Harbor completed without a verifier result")

    rewards = verifier_result.rewards
    if not isinstance(rewards, dict) or not rewards:
        context.fail_validator("Harbor verifier did not produce a usable reward payload")

    raw_reward: Any
    if "reward" in rewards:
        raw_reward = rewards["reward"]

    elif len(rewards) == 1:
        raw_reward = next(iter(rewards.values()))

    else:
        context.fail_validator(
            f"Harbor verifier produced an unsupported multi-metric reward payload: {json.dumps(rewards, sort_keys=True)}",
        )

    if isinstance(raw_reward, bool) or not isinstance(raw_reward, (int, float)):
        context.fail_validator(f"Harbor verifier produced a non-numeric reward value: {raw_reward!r}")

    return float(raw_reward)


def parse_structured_test_results(
    verifier_test_results_path: Path,
    artifact_test_results_path: Path,
    verifier_report_path: Path,
    artifact_report_path: Path,
    *,
    context: FailureContext,
) -> list[ProblemTestResult]:
    """Parse Ridges-shaped test_results.json, falling back to SWE-Bench report.json.

    Returns an empty list when nothing is found. A present-but-malformed
    test_results.json becomes a classified validator failure.
    """

    for path in (verifier_test_results_path, artifact_test_results_path):
        test_results_payload = read_json_artifact(path, context=context)
        if test_results_payload is None:
            continue

        try:
            return VerifierTestResultsPayload.model_validate(test_results_payload).output
        except ValidationError as exception:
            context.fail_validator(
                f"Harbor produced malformed verifier test_results.json at {path}: {exception}",
                cause=exception,
            )

    return parse_report_based_test_results(
        verifier_report_path,
        artifact_report_path,
        context=context,
    )


def parse_report_based_test_results(
    verifier_report_path: Path,
    artifact_report_path: Path,
    *,
    context: FailureContext,
) -> list[ProblemTestResult]:
    """Parse a SWE-Bench report.json into Ridges test results, or return []."""
    for path in (verifier_report_path, artifact_report_path):
        report_payload = read_json_artifact(path, context=context)
        if report_payload is None:
            continue

        parsed_results = test_results_from_swebench_report(report_payload)
        if parsed_results:
            return parsed_results

    return []


def test_results_from_swebench_report(payload: Any) -> list[ProblemTestResult]:
    """Translate a SWE-Bench report.json structure into Ridges test results.

    Only reads 'tests_status' with FAIL_TO_PASS / PASS_TO_PASS buckets and
    success / failure outcomes. Returns [] when the shape doesn't match.
    """
    if not isinstance(payload, dict):
        return []

    category_map = {
        "FAIL_TO_PASS": ProblemTestCategory.fail_to_pass,
        "PASS_TO_PASS": ProblemTestCategory.pass_to_pass,
    }
    outcome_map = {
        "success": ProblemTestResultStatus.PASS,
        "failure": ProblemTestResultStatus.FAIL,
    }

    results: list[ProblemTestResult] = []
    for problem_report in payload.values():
        if not isinstance(problem_report, dict):
            continue

        tests_status = problem_report.get("tests_status")
        if not isinstance(tests_status, dict):
            continue

        for report_category, category in category_map.items():
            bucket = tests_status.get(report_category)
            if not isinstance(bucket, dict):
                continue

            for outcome_key, status in outcome_map.items():
                names = bucket.get(outcome_key)
                if not isinstance(names, list):
                    continue

                for name in names:
                    if isinstance(name, str):
                        results.append(
                            ProblemTestResult(
                                name=name,
                                category=category,
                                status=status,
                            )
                        )

    return results


# Report discovery


def render_discovered_report(*, trial_paths: TrialPaths) -> str:
    """Return a '# discovered_verifier_report' section, or '' when no report is found.

    Pretty-prints JSON and truncates beyond 20k characters.
    """
    report_path = discover_verifier_report(trial_paths=trial_paths)
    if report_path is None:
        return ""

    display_path: str
    try:
        display_path = report_path.relative_to(trial_paths.trial_dir).as_posix()
    except ValueError:
        display_path = str(report_path)

    try:
        content = report_path.read_text(errors="replace").strip()
    except OSError:
        return f"# discovered_verifier_report\npath: {display_path}\n<unreadable report>"

    if not content:
        rendered_content = "<empty report>"
    else:
        if report_path.suffix == ".json":
            try:
                rendered_content = json.dumps(json.loads(content), indent=2, sort_keys=True)
            except json.JSONDecodeError:
                rendered_content = content

        else:
            rendered_content = content

        if len(rendered_content) > 20_000:
            rendered_content = rendered_content[:20_000] + "\n<truncated>"

    return f"# discovered_verifier_report\npath: {display_path}\n{rendered_content}"


def discover_verifier_report(*, trial_paths: TrialPaths) -> Path | None:
    """Pick the verifier report under the trial dir.

    Scans the verifier dir first, then the artifacts dir. Returns the first
    preferred-name match, falling back to any other JSON or XML file.
    """
    preferred_names = (
        "report.json",
        "results.json",
        "junit.xml",
        "pytest.xml",
    )
    ignored_names = {"manifest.json", "reward.json", "test_results.json"}
    search_roots = (
        trial_paths.verifier_dir,
        trial_paths.artifacts_dir,
    )

    for root in search_roots:
        if not root.exists():
            continue

        for filename in preferred_names:
            for candidate in sorted(root.rglob(filename)):
                if candidate.is_file():
                    return candidate

    for root in search_roots:
        if not root.exists():
            continue

        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue

            if candidate.suffix not in {".json", ".xml"}:
                continue

            if candidate.name in ignored_names:
                continue

            return candidate

    return None


# Evaluation log reading


def read_eval_logs(*, trial_paths: TrialPaths) -> str:
    """Return Harbor's verifier stdout merged with the discovered report body."""
    stdout_logs = read_text(trial_paths.test_stdout_path)
    report_logs = render_discovered_report(trial_paths=trial_paths)
    return merge_logs(stdout_logs, report_logs)


def _read_eval_logs_best_effort(*, trial_paths: TrialPaths) -> str:
    """Best-effort 'read_eval_logs' that never raises, so crash context is never masked."""
    stdout_logs = _read_text_best_effort(trial_paths.test_stdout_path)
    try:
        report_logs = render_discovered_report(trial_paths=trial_paths)
    except Exception:
        report_logs = ""

    return merge_logs(stdout_logs, report_logs)


# I/O helpers


def read_text(path: Path) -> str:
    """Read a file if it exists, otherwise return ''."""
    if not path.exists():
        return ""
    return path.read_text()


def _read_text_best_effort(path: Path) -> str:
    """Best-effort 'read_text' that swallows OSError."""
    try:
        return read_text(path)
    except OSError:
        return ""


def read_json(path: Path) -> Any | None:
    """Read and decode JSON if the file exists; return None when it's missing."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def read_json_artifact(path: Path, *, context: FailureContext) -> Any | None:
    """Read a JSON artifact; malformed JSON becomes a classified validator failure."""
    try:
        return read_json(path)
    except json.JSONDecodeError as exception:
        context.fail_validator(
            f"Harbor produced malformed JSON artifact at {path}: {exception}",
            cause=exception,
        )
