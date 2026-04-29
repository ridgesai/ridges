# NOTE ADAM: Subtensor bug (self.disable_third_party_loggers())
import asyncio
import os
import pathlib
import random
import sys
import time
import traceback
from typing import Any, Dict
from uuid import UUID

import httpx

import utils.logger as logger
import validator.config as config
from api.endpoints.validator_models import (
    ScreenerRegistrationRequest,
    ScreenerRegistrationResponse,
    ValidatorDisconnectRequest,
    ValidatorFinishEvaluationRequest,
    ValidatorHeartbeatRequest,
    ValidatorRegistrationRequest,
    ValidatorRegistrationResponse,
    ValidatorRequestEvaluationRequest,
    ValidatorRequestEvaluationResponse,
    ValidatorTaskDownloadUrlRequest,
    ValidatorUpdateEvaluationRunRequest,
)
from execution.engine import ExecutionEngine
from execution.errors import EvaluationRunException
from execution.types import TrialSnapshot
from models.evaluation_run import EvaluationRunErrorCode, EvaluationRunStatus
from models.problem import ProblemTestResultStatus
from utils.docker import cleanup_harbor_docker_resources, prune_docker_disk_resources
from utils.git import COMMIT_HASH, reset_local_repo
from utils.system_metrics import get_system_metrics
from validator.http_utils import get_ridges_platform, post_ridges_platform
from validator.set_weights import set_weights_from_mapping

# The session ID for this validator
session_id = None
running_agent_timeout_seconds = None
running_eval_timeout_seconds = None
max_evaluation_run_log_size_bytes = None


execution_engine = None
STATUS_HOOK_TIMEOUT_SECONDS = 5


# Disconnect from the Ridges platform (called when the program exits)
async def disconnect(reason: str):
    if session_id is None:
        return

    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform(
            "/validator/disconnect", ValidatorDisconnectRequest(reason=reason), bearer_token=session_id
        )
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error in disconnect(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)


# A loop that sends periodic heartbeats to the Ridges platform
async def send_heartbeat_loop():
    try:
        logger.info("Starting send heartbeat loop...")
        while True:
            logger.info("Sending heartbeat...")
            system_metrics = await get_system_metrics()
            await post_ridges_platform(
                "/validator/heartbeat",
                ValidatorHeartbeatRequest(system_metrics=system_metrics),
                bearer_token=session_id,
                quiet=2,
                timeout=5,
            )
            await asyncio.sleep(config.SEND_HEARTBEAT_INTERVAL_SECONDS)
    except Exception as e:
        logger.error(f"Error in send_heartbeat_loop(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)


# A loop that periodically sets weights
async def set_weights_loop():
    logger.info("Starting set weights loop...")
    while True:
        weights_mapping = await get_ridges_platform("/scoring/weights", quiet=1)

        try:
            await asyncio.wait_for(
                set_weights_from_mapping(weights_mapping), timeout=config.SET_WEIGHTS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as e:
            logger.error(f"asyncio.TimeoutError in set_weights_from_mapping(): {e}")

        await asyncio.sleep(config.SET_WEIGHTS_INTERVAL_SECONDS)


# Sends an update-evaluation-run request to the Ridges platform. The extra
# parameter is for fields that are not sent in all requests, such as agent_logs
# and eval_logs, which are only sent on some state transitions.
async def update_evaluation_run(
    evaluation_run_id: UUID,
    problem_name: str,
    updated_status: EvaluationRunStatus,
    extra: Dict[str, Any] | None = None,
    *,
    timeout: int | None = None,
):
    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to {updated_status.value}...")

    post_kwargs: dict[str, Any] = {
        "bearer_token": session_id,
        "quiet": 2,
    }
    if timeout is not None:
        post_kwargs["timeout"] = timeout

    await post_ridges_platform(
        "/validator/update-evaluation-run",
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run_id, updated_status=updated_status, **(extra or {})
        ),
        **post_kwargs,
    )


# Truncates a log if required
def truncate_logs_if_required(log: str) -> str:
    if len(log) > max_evaluation_run_log_size_bytes:
        return (
            f"<truncated {len(log) - max_evaluation_run_log_size_bytes} chars>\n\n"
            + log[-max_evaluation_run_log_size_bytes:]
        )
    return log


async def _fetch_task_download_url(task_digest: str) -> str:
    """Ask the platform for a fresh presigned URL for a task archive."""
    try:
        resp = await post_ridges_platform(
            "/validator/task-download-url",
            ValidatorTaskDownloadUrlRequest(task_digest=task_digest),
            bearer_token=session_id,
            quiet=2,
        )
        url = resp.get("url") if isinstance(resp, dict) else None
        if not url:
            raise EvaluationRunException(
                EvaluationRunErrorCode.PLATFORM_FAILED_PROVISIONING,
                f"Platform returned no URL for task digest {task_digest}: {resp}",
            )
        return url
    except httpx.HTTPStatusError as exc:
        raise EvaluationRunException(
            EvaluationRunErrorCode.PLATFORM_FAILED_PROVISIONING,
            f"Platform failed to provide download URL for task digest {task_digest}: "
            f"{exc.response.status_code} {exc.response.text}",
        ) from exc
    except Exception as exc:
        raise EvaluationRunException(
            EvaluationRunErrorCode.PLATFORM_FAILED_PROVISIONING,
            f"Failed to fetch download URL for task digest {task_digest}: {type(exc).__name__}: {exc}",
        ) from exc


async def _upload_job_artifacts(job_dir: pathlib.Path, upload_url: str) -> None:
    """Tar the job directory and PUT it to a presigned S3 URL. Best-effort."""
    try:
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(str(job_dir), arcname=job_dir.name)
        buf.seek(0)
        payload = buf.read()

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(upload_url, content=payload)
            resp.raise_for_status()

        logger.info(f"Uploaded {len(payload)} bytes of job artifacts to S3")
    except Exception as exc:
        logger.warning(f"Failed to upload job artifacts (best-effort): {exc}")


async def _simulate_run_evaluation_run_with_semaphore(
    evaluation_run_id: UUID, problem_name: str, semaphore: asyncio.Semaphore
):
    async with semaphore:
        return await _simulate_run_evaluation_run(evaluation_run_id, problem_name)


# Simulate a run of an evaluation run, useful for testing, set SIMULATE_EVALUATION_RUNS=True in .env
async def _simulate_run_evaluation_run(evaluation_run_id: UUID, problem_name: str):
    logger.info(f"Starting simulated evaluation run {evaluation_run_id} for problem {problem_name}...")

    # Move from pending -> initializing_agent
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_agent)

    # Move from initializing_agent -> running_agent
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_agent)

    # Move from running_agent -> initializing_eval
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(
        evaluation_run_id,
        problem_name,
        EvaluationRunStatus.initializing_eval,
        {"patch": "FAKE PATCH", "agent_logs": "FAKE AGENT LOGS"},
    )

    # Move from initializing_eval -> running_eval
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_eval)

    # Move from running_eval -> finished
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(
        evaluation_run_id,
        problem_name,
        EvaluationRunStatus.finished,
        {
            "test_results": [
                {"name": "fake_test", "category": "default", "status": f"{ProblemTestResultStatus.PASS.value}"}
            ],
            "verifier_reward": 1.0,
            "eval_logs": "FAKE EVAL LOGS",
        },
    )

    logger.info(f"Finished simulated evaluation run {evaluation_run_id} for problem {problem_name}")


async def _run_evaluation_run_with_semaphore(
    evaluation_run,
    agent_code: str,
    semaphore: asyncio.Semaphore,
    artifact_upload_url: str | None = None,
):
    async with semaphore:
        return await _run_evaluation_run(evaluation_run, agent_code, artifact_upload_url=artifact_upload_url)


# Run an evaluation run
async def _run_evaluation_run(evaluation_run, agent_code: str, artifact_upload_url: str | None = None):
    try:
        global execution_engine
        assert execution_engine is not None

        evaluation_run_id = evaluation_run.evaluation_run_id
        problem_name = evaluation_run.problem_name
        logger.info(f"Starting evaluation run {evaluation_run_id} for problem {problem_name}...")

        job_dir = None

        try:
            # Move from pending -> initializing_agent
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_agent)

            async def _on_agent_started() -> None:
                await update_evaluation_run(
                    evaluation_run_id,
                    problem_name,
                    EvaluationRunStatus.running_agent,
                    timeout=STATUS_HOOK_TIMEOUT_SECONDS,
                )

            async def _on_verification_started(snapshot: TrialSnapshot) -> None:
                await update_evaluation_run(
                    evaluation_run_id,
                    problem_name,
                    EvaluationRunStatus.initializing_eval,
                    {
                        "patch": snapshot.patch,
                        "agent_logs": truncate_logs_if_required(snapshot.agent_logs),
                    },
                    timeout=STATUS_HOOK_TIMEOUT_SECONDS,
                )

                await update_evaluation_run(
                    evaluation_run_id,
                    problem_name,
                    EvaluationRunStatus.running_eval,
                    timeout=STATUS_HOOK_TIMEOUT_SECONDS,
                )

            result = await execution_engine.evaluate(
                evaluation_run_id=evaluation_run_id,
                problem_name=problem_name,
                execution_spec=evaluation_run.execution_spec,
                agent_path=None,
                agent_code=agent_code,
                fetch_task_url=_fetch_task_download_url,
                on_agent_started=_on_agent_started,
                on_verification_started=_on_verification_started,
            )
            job_dir = result.job_dir

            logger.info(
                f"Finished {result.backend} execution for problem {problem_name}: "
                f"{len(result.patch.splitlines())} lines of patch, "
                f"{len(result.agent_logs.splitlines())} lines of agent logs, "
                f"{len(result.eval_logs.splitlines())} lines of eval logs"
            )

            num_passed = sum(1 for test in result.test_results if test.status == ProblemTestResultStatus.PASS)
            num_failed = sum(1 for test in result.test_results if test.status == ProblemTestResultStatus.FAIL)
            num_skipped = sum(1 for test in result.test_results if test.status == ProblemTestResultStatus.SKIP)
            logger.info(
                f"Finished running evaluation for problem {problem_name}: "
                f"reward={result.verifier_reward}, {len(result.test_results)} test results "
                f"({num_passed} passed, {num_failed} failed, {num_skipped} skipped), "
                f"{len(result.eval_logs.splitlines())} lines of eval logs"
            )

            # Move from running_eval -> finished
            await update_evaluation_run(
                evaluation_run_id,
                problem_name,
                EvaluationRunStatus.finished,
                {
                    "patch": result.patch,
                    "agent_logs": truncate_logs_if_required(result.agent_logs),
                    "verifier_reward": result.verifier_reward,
                    "test_results": [
                        test.model_dump(exclude={"test_alias"}, exclude_none=True) for test in result.test_results
                    ],
                    "eval_logs": truncate_logs_if_required(result.eval_logs),
                },
            )

        except EvaluationRunException as e:
            logger.error(f"Evaluation run {evaluation_run_id} for problem {problem_name} errored: {e}")
            extra = dict(e.extra or {})
            job_dir = extra.pop("job_dir", None)
            for key in ("agent_logs", "eval_logs"):
                if key in extra:
                    extra[key] = truncate_logs_if_required(extra[key])

            await update_evaluation_run(
                evaluation_run_id,
                problem_name,
                EvaluationRunStatus.error,
                {
                    "error_code": e.error_code.value,
                    "error_message": e.error_message,
                    **extra,
                },
            )

        except Exception as e:
            logger.error(
                f"Evaluation run {evaluation_run_id} for problem {problem_name} errored: {EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()}: {e}"
            )
            logger.error(traceback.format_exc())

            await update_evaluation_run(
                evaluation_run_id,
                problem_name,
                EvaluationRunStatus.error,
                {
                    "error_code": EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value,
                    "error_message": (
                        f"{EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()}: {e}\n\n"
                        f"Traceback:\n{traceback.format_exc()}"
                    ),
                },
            )

        # Upload artifacts for both success and error cases
        if artifact_upload_url and job_dir:
            await _upload_job_artifacts(job_dir, artifact_upload_url)

        logger.info(f"Finished evaluation run {evaluation_run_id} for problem {problem_name}")

    except Exception as e:
        logger.error(f"Error in _run_evaluation_run(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)


# Run an evaluation, automatically dispatches all runs to either _simulate_run_evaluation_run or _run_evaluation_run
async def _run_evaluation(request_evaluation_response: ValidatorRequestEvaluationResponse):
    logger.info("Received evaluation:")
    logger.info(f"  # of evaluation runs: {len(request_evaluation_response.evaluation_runs)}")

    for evaluation_run in request_evaluation_response.evaluation_runs:
        logger.info(f"    {evaluation_run.problem_name}")

    logger.info("Starting evaluation...")

    tasks = []
    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_EVALUATION_RUNS)
    for evaluation_run in request_evaluation_response.evaluation_runs:
        evaluation_run_id = evaluation_run.evaluation_run_id
        problem_name = evaluation_run.problem_name

        if config.SIMULATE_EVALUATION_RUNS:
            tasks.append(
                asyncio.create_task(
                    _simulate_run_evaluation_run_with_semaphore(evaluation_run_id, problem_name, semaphore)
                )
            )
        else:
            upload_url = request_evaluation_response.artifact_upload_urls.get(str(evaluation_run_id))
            tasks.append(
                asyncio.create_task(
                    _run_evaluation_run_with_semaphore(
                        evaluation_run,
                        request_evaluation_response.agent_code,
                        semaphore,
                        artifact_upload_url=upload_url,
                    )
                )
            )

    try:
        await asyncio.gather(*tasks)

        logger.info("Finished evaluation")

        await post_ridges_platform(
            "/validator/finish-evaluation", ValidatorFinishEvaluationRequest(), bearer_token=session_id, quiet=1
        )
    finally:
        await asyncio.to_thread(prune_docker_disk_resources)


# Main loop
async def main():
    global session_id
    global running_agent_timeout_seconds
    global running_eval_timeout_seconds
    global max_evaluation_run_log_size_bytes
    global execution_engine

    cleanup_harbor_docker_resources()
    prune_docker_disk_resources(include_build_cache=True)

    # Register with the Ridges platform, yielding us a session ID
    logger.info("Registering validator...")

    try:
        if config.MODE == "validator":
            # Get the current timestamp, and sign it with the validator hotkey
            timestamp = int(time.time())
            signed_timestamp = config.VALIDATOR_HOTKEY.sign(str(timestamp)).hex()

            register_response = ValidatorRegistrationResponse(
                **(
                    await post_ridges_platform(
                        "/validator/register-as-validator",
                        ValidatorRegistrationRequest(
                            timestamp=timestamp,
                            signed_timestamp=signed_timestamp,
                            hotkey=config.VALIDATOR_HOTKEY.ss58_address,
                            commit_hash=COMMIT_HASH,
                        ),
                    )
                )
            )

        elif config.MODE == "screener":
            register_response = ScreenerRegistrationResponse(
                **(
                    await post_ridges_platform(
                        "/validator/register-as-screener",
                        ScreenerRegistrationRequest(
                            name=config.SCREENER_NAME, password=config.SCREENER_PASSWORD, commit_hash=COMMIT_HASH
                        ),
                    )
                )
            )

    except httpx.HTTPStatusError as e:
        if config.UPDATE_AUTOMATICALLY and e.response.status_code == 426:
            logger.info("Updating...")
            reset_local_repo(pathlib.Path(__file__).parent.parent, e.response.headers["X-Commit-Hash"])
            sys.exit(0)
        else:
            raise e

    session_id = register_response.session_id
    running_agent_timeout_seconds = register_response.running_agent_timeout_seconds
    running_eval_timeout_seconds = register_response.running_eval_timeout_seconds
    max_evaluation_run_log_size_bytes = register_response.max_evaluation_run_log_size_bytes

    logger.info("Registered validator:")
    logger.info(f"  Session ID: {session_id}")
    logger.info(f"  Running Agent Timeout: {running_agent_timeout_seconds} second(s)")
    logger.info(f"  Running Evaluation Timeout: {running_eval_timeout_seconds} second(s)")
    logger.info(f"  Max Evaluation Run Log Size: {max_evaluation_run_log_size_bytes} byte(s)")

    execution_engine = ExecutionEngine(
        inference_url=config.RIDGES_INFERENCE_GATEWAY_URL,
        harbor_results_dir=config.RIDGES_HARBOR_RESULTS_DIR,
        harbor_debug=config.RIDGES_HARBOR_DEBUG,
    )

    # Start the send heartbeat loop
    asyncio.create_task(send_heartbeat_loop())

    if config.MODE == "validator":
        # Start the set weights loop
        asyncio.create_task(set_weights_loop())

    # Loop forever, just keep requesting evaluations and running them
    while True:
        logger.info("Requesting an evaluation...")

        request_evaluation_response_data = await post_ridges_platform(
            "/validator/request-evaluation", ValidatorRequestEvaluationRequest(), bearer_token=session_id, quiet=1
        )

        # If no evaluation is available, wait and try again
        if request_evaluation_response_data is None:
            logger.info(
                f"No evaluations available. Waiting for {config.REQUEST_EVALUATION_INTERVAL_SECONDS} seconds..."
            )
            await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)
            continue

        await _run_evaluation(ValidatorRequestEvaluationResponse(**request_evaluation_response_data))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
        asyncio.run(disconnect("Keyboard interrupt"))
        os._exit(1)
    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        asyncio.run(disconnect(f"Error in main(): {type(e).__name__}: {e}"))
        os._exit(1)
