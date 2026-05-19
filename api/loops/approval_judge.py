import asyncio
import socket
from uuid import uuid4

import httpx
from pydantic import ValidationError

import api.config as config
import utils.logger as logger
from models.approval import (
    ApprovalInputSnapshot,
    ApprovalJudgeRequest,
    ApprovalJudgeResponse,
    ApprovalJudgeRetryableFailure,
)
from queries.approval import (
    claim_next_approval_job,
    finalize_approval_job,
    move_exhausted_approval_jobs_to_review,
    record_approval_job_error,
)
from utils.s3 import generate_presigned_url

CLAIMED_BY = f"{socket.gethostname()}:{uuid4()}"


class RetryableJudgeServiceError(RuntimeError):
    """The judge service could not complete all rounds and asked the platform to retry."""


async def approval_judge_loop() -> None:
    """Poll for approval jobs and send them to the multi-round judge service."""

    if config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS >= config.JUDGE_SERVICE_JOB_LEASE_SECONDS:
        logger.fatal(
            "PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS must be less than "
            f"the hardcoded judge job lease ({config.JUDGE_SERVICE_JOB_LEASE_SECONDS} seconds)"
        )

    logger.info(
        f"Starting approval judge loop: url={config.PRE_SCREENING_JUDGE_URL} "
        f"interval_seconds={config.JUDGE_SERVICE_POLL_INTERVAL_SECONDS} "
        f"timeout_seconds={config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS} "
        f"lease_seconds={config.JUDGE_SERVICE_JOB_LEASE_SECONDS} "
        f"max_attempts={config.JUDGE_SERVICE_MAX_ATTEMPTS} "
        f"claimed_by={CLAIMED_BY}"
    )

    while True:
        try:
            await process_next_approval_job()
        except Exception as exc:
            logger.error(f"Unexpected error in approval judge loop: {type(exc).__name__}: {exc}")

        await asyncio.sleep(config.JUDGE_SERVICE_POLL_INTERVAL_SECONDS)


async def process_next_approval_job() -> None:
    """Claim one approval job, judge it once, and persist the terminal or retryable outcome."""

    await move_exhausted_approval_jobs_to_review(max_attempts=config.JUDGE_SERVICE_MAX_ATTEMPTS)

    job = await claim_next_approval_job(
        claimed_by=CLAIMED_BY,
        lease_seconds=config.JUDGE_SERVICE_JOB_LEASE_SECONDS,
        max_attempts=config.JUDGE_SERVICE_MAX_ATTEMPTS,
    )
    if job is None:
        return
    if job.claim_token is None:
        logger.error(f"Claimed approval job {job.job_id} without a claim token")
        return

    try:
        snapshot = ApprovalInputSnapshot.model_validate(job.input_snapshot)
        source_url = await generate_presigned_url(
            snapshot.source.key,
            ttl_seconds=config.JUDGE_SERVICE_JOB_LEASE_SECONDS,
        )
        request_body = ApprovalJudgeRequest(
            job_id=job.job_id,
            agent_id=job.agent_id,
            set_id=job.set_id,
            source={
                "type": "presigned_url",
                "url": source_url,
                "file": snapshot.source.file,
            },
            policy_version=job.policy_version,
            evaluation_context=snapshot.evaluation_context,
        )

        result = await _call_judge_service(request_body)
        if result.policy_version != job.policy_version:
            raise RuntimeError(
                f"Judge policy version mismatch: expected {job.policy_version}, got {result.policy_version}"
            )

        finalized = await finalize_approval_job(
            job_id=job.job_id,
            claim_token=job.claim_token,
            result=result,
        )
        if not finalized:
            logger.warning(f"Approval job {job.job_id} was not finalized because its claim is stale")
        else:
            logger.info(
                f"Approval judge completed: job_id={job.job_id} agent_id={job.agent_id} set_id={job.set_id} "
                f"verdict={result.aggregate_verdict.value} score={result.aggregate_score:.2f} "
                f"confidence={result.aggregate_confidence:.2f}"
            )

    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error(f"Approval judge job {job.job_id} failed: {error_message}")
        recorded = await record_approval_job_error(
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_message=error_message,
            backoff_seconds=config.JUDGE_SERVICE_ERROR_BACKOFF_SECONDS,
            max_attempts=config.JUDGE_SERVICE_MAX_ATTEMPTS,
        )
        if not recorded:
            logger.warning(f"Approval job {job.job_id} error was not recorded because its claim is stale")


async def _call_judge_service(request_body: ApprovalJudgeRequest) -> ApprovalJudgeResponse:
    """Call the approval endpoint and distinguish terminal vs retryable failures."""

    headers = {"Authorization": f"Bearer {config.PRE_SCREENING_JUDGE_INTERNAL_TOKEN}"}
    timeout = httpx.Timeout(config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.PRE_SCREENING_JUDGE_URL.rstrip('/')}/judge-approval",
            json=request_body.model_dump(mode="json"),
            headers=headers,
        )

    if response.status_code == 503:
        try:
            retryable = ApprovalJudgeRetryableFailure.model_validate(response.json())
            raise RetryableJudgeServiceError(f"{retryable.error_code}: {retryable.message}")
        except ValidationError:
            raise RetryableJudgeServiceError(f"judge returned 503: {response.text[:500]}") from None

    if response.status_code != 200:
        raise RuntimeError(f"Judge service returned {response.status_code}: {response.text[:500]}")

    try:
        return ApprovalJudgeResponse.model_validate(response.json())
    except ValidationError as exc:
        raise RuntimeError(f"Judge service returned invalid response: {exc}") from exc
