import asyncio
import logging
import socket
from uuid import uuid4

import httpx
from pydantic import ValidationError

import api.config as config
from models.pre_screening_judge import (
    PreScreeningJudgeRequest,
    PreScreeningJudgeResponse,
    PreScreeningResultPayload,
)
from queries.pre_screening_judge import (
    claim_next_pre_screening_job,
    finalize_pre_screening_job,
    move_exhausted_pre_screening_jobs_to_review,
    record_pre_screening_job_error,
)
from utils.logger import fatal
from utils.s3 import generate_presigned_url

logger = logging.getLogger(__name__)

CLAIMED_BY = f"{socket.gethostname()}:{uuid4()}"
POLL_INTERVAL_SECONDS = 10
JOB_LEASE_SECONDS = 840
MAX_ATTEMPTS = 3
ERROR_BACKOFF_SECONDS = 60


async def pre_screening_judge_loop() -> None:
    """Poll for pre-screening jobs and send them to the judge service."""

    if config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS >= JOB_LEASE_SECONDS:
        fatal(
            "PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS must be less than "
            f"the hardcoded pre-screening judge job lease ({JOB_LEASE_SECONDS} seconds)"
        )

    logger.info(
        "Pre-screening judge loop started",
        extra={
            "url": config.PRE_SCREENING_JUDGE_URL,
            "interval_s": POLL_INTERVAL_SECONDS,
            "timeout_s": config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS,
            "lease_s": JOB_LEASE_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
            "claimed_by": CLAIMED_BY,
        },
    )

    while True:
        try:
            await process_next_pre_screening_job()
        except Exception as exc:
            logger.error("Pre-screening judge loop error", extra={"error": f"{type(exc).__name__}: {exc}"})

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def process_next_pre_screening_job() -> None:
    """Claim one eligible job, run it through the judge, and persist the outcome."""

    await move_exhausted_pre_screening_jobs_to_review(max_attempts=MAX_ATTEMPTS)

    job = await claim_next_pre_screening_job(
        claimed_by=CLAIMED_BY,
        lease_seconds=JOB_LEASE_SECONDS,
        max_attempts=MAX_ATTEMPTS,
    )
    if job is None:
        return
    if job.claim_token is None:
        logger.error("Claimed pre-screening job without claim token", extra={"job_id": str(job.job_id)})
        return

    try:
        source_url = await generate_presigned_url(
            f"{job.agent_id}/agent.py",
            ttl_seconds=JOB_LEASE_SECONDS,
        )
        request_body = PreScreeningJudgeRequest(
            job_id=job.job_id,
            agent_id=job.agent_id,
            source={
                "type": "presigned_url",
                "url": source_url,
                "file": "agent.py",
            },
            policy_version=job.policy_version,
        )

        result = await _call_judge_service(request_body)
        if result.policy_version != job.policy_version:
            raise RuntimeError(
                f"Judge policy version mismatch: expected {job.policy_version}, got {result.policy_version}"
            )

        finalized = await finalize_pre_screening_job(
            job_id=job.job_id,
            claim_token=job.claim_token,
            result=_result_payload(result),
        )
        if not finalized:
            logger.warning("Pre-screening job not finalized (stale claim)", extra={"job_id": str(job.job_id)})
        else:
            logger.info(
                "Pre-screening judge completed",
                extra={
                    "job_id": str(job.job_id),
                    "agent_id": str(job.agent_id),
                    "verdict": result.verdict.value,
                    "confidence": round(result.confidence, 2),
                },
            )

    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("Pre-screening judge job failed", extra={"job_id": str(job.job_id), "error": error_message})
        recorded = await record_pre_screening_job_error(
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_message=error_message,
            backoff_seconds=ERROR_BACKOFF_SECONDS,
            max_attempts=MAX_ATTEMPTS,
            policy_version=job.policy_version,
        )

        if not recorded:
            logger.warning("Pre-screening job error not recorded (stale claim)", extra={"job_id": str(job.job_id)})


async def _call_judge_service(request_body: PreScreeningJudgeRequest) -> PreScreeningJudgeResponse:
    """Call the hardcoding judge service and validate its structured response."""

    headers = {"Authorization": f"Bearer {config.PRE_SCREENING_JUDGE_INTERNAL_TOKEN}"}
    timeout = httpx.Timeout(config.PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.PRE_SCREENING_JUDGE_URL.rstrip('/')}/judge-agent",
            json=request_body.model_dump(mode="json"),
            headers=headers,
        )

    if response.status_code != 200:
        raise RuntimeError(f"Judge service returned {response.status_code}: {response.text[:500]}")

    try:
        return PreScreeningJudgeResponse.model_validate(response.json())
    except ValidationError as exc:
        raise RuntimeError(f"Judge service returned invalid response: {exc}") from exc


def _result_payload(result: PreScreeningJudgeResponse) -> PreScreeningResultPayload:
    """Convert a judge response into the result shape stored by the platform."""

    raw_response = result.model_dump(mode="json")
    return PreScreeningResultPayload(
        verdict=result.verdict,
        confidence=result.confidence,
        summary=result.summary,
        categories=result.categories,
        evidence=[evidence.model_dump(mode="json") for evidence in result.evidence],
        static_findings=result.static_findings,
        model=result.model,
        fallback_used=result.fallback_used,
        policy_version=result.policy_version,
        source_sha256=result.source_sha256,
        raw_response=raw_response,
        error_message=None,
    )
