from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

import validator.main as validator_main
from api.endpoints.validator_models import (
    ValidatorRequestEvaluationResponse,
    ValidatorRequestEvaluationResponseEvaluationRun,
)


def _request_response(*, run_count: int = 1) -> ValidatorRequestEvaluationResponse:
    return ValidatorRequestEvaluationResponse(
        evaluation_id=uuid4(),
        agent_id=uuid4(),
        agent_code="print('agent')",
        evaluation_runs=[
            ValidatorRequestEvaluationResponseEvaluationRun(
                evaluation_run_id=uuid4(),
                problem_name=f"problem-{idx}",
            )
            for idx in range(run_count)
        ],
    )


@pytest.mark.anyio
async def test_cancellation_poll_sets_event_when_platform_requests_cancel(monkeypatch) -> None:
    event = asyncio.Event()
    reason = {"reason": None}

    async def fake_post(_endpoint, _body, **_kwargs):
        return {"should_cancel": True, "reason": "score bound"}

    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)

    await validator_main._poll_evaluation_cancellation(uuid4(), uuid4(), event, reason)

    assert event.is_set()
    assert reason["reason"] == "score bound"


@pytest.mark.anyio
async def test_cancellation_poll_continues_after_mismatch(monkeypatch) -> None:
    event = asyncio.Event()
    reason = {"reason": None}
    calls = 0

    async def fake_post(_endpoint, _body, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", "http://platform/validator/check-cancellation")
            response = httpx.Response(409, request=request, text="mismatch")
            raise httpx.HTTPStatusError("mismatch", request=request, response=response)
        return {"should_cancel": True, "reason": "score bound"}

    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)
    monkeypatch.setattr(validator_main.config, "VALIDATOR_CANCELLATION_CHECK_INTERVAL_SECONDS", 0.01)

    await validator_main._poll_evaluation_cancellation(uuid4(), uuid4(), event, reason)

    assert calls == 2
    assert event.is_set()
    assert reason["reason"] == "score bound"


@pytest.mark.anyio
async def test_run_evaluation_cancels_tasks_and_calls_cancel_endpoint(monkeypatch) -> None:
    response = _request_response(run_count=2)
    posted_endpoints: list[str] = []
    cancelled_runs = 0

    async def fake_poll(_evaluation_id, _agent_id, cancellation_event, cancellation_reason):
        await asyncio.sleep(0)
        cancellation_reason["reason"] = "score bound"
        cancellation_event.set()

    async def fake_run(*_args, **_kwargs):
        nonlocal cancelled_runs
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled_runs += 1
            raise

    async def fake_post(endpoint, _body, **_kwargs):
        posted_endpoints.append(endpoint)
        return {}

    monkeypatch.setattr(validator_main.config, "MODE", "validator")
    monkeypatch.setattr(validator_main.config, "SIMULATE_EVALUATION_RUNS", False)
    monkeypatch.setattr(validator_main.config, "MAX_CONCURRENT_EVALUATION_RUNS", 2)
    monkeypatch.setattr(validator_main, "_poll_evaluation_cancellation", fake_poll)
    monkeypatch.setattr(validator_main, "_run_evaluation_run_with_semaphore", fake_run)
    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)
    monkeypatch.setattr(validator_main, "prune_docker_disk_resources", lambda: None)

    await validator_main._run_evaluation(response)

    assert posted_endpoints == ["/validator/cancel-current-evaluation"]
    assert cancelled_runs == 2


@pytest.mark.anyio
async def test_run_evaluation_polls_for_cancellation_in_screener_mode(monkeypatch) -> None:
    response = _request_response(run_count=1)
    posted_endpoints: list[str] = []

    async def fake_poll(_evaluation_id, _agent_id, cancellation_event, cancellation_reason):
        cancellation_reason["reason"] = "score bound"
        cancellation_event.set()

    async def fake_run(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def fake_post(endpoint, _body, **_kwargs):
        posted_endpoints.append(endpoint)
        return {}

    monkeypatch.setattr(validator_main.config, "MODE", "screener")
    monkeypatch.setattr(validator_main.config, "SIMULATE_EVALUATION_RUNS", False)
    monkeypatch.setattr(validator_main.config, "MAX_CONCURRENT_EVALUATION_RUNS", 1)
    monkeypatch.setattr(validator_main, "_poll_evaluation_cancellation", fake_poll)
    monkeypatch.setattr(validator_main, "_run_evaluation_run_with_semaphore", fake_run)
    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)
    monkeypatch.setattr(validator_main, "prune_docker_disk_resources", lambda: None)

    await validator_main._run_evaluation(response)

    assert posted_endpoints == ["/validator/cancel-current-evaluation"]


@pytest.mark.anyio
async def test_run_evaluation_normal_completion_calls_finish_endpoint(monkeypatch) -> None:
    response = _request_response(run_count=1)
    posted_endpoints: list[str] = []

    async def fake_poll(_evaluation_id, _agent_id, cancellation_event, _cancellation_reason):
        await cancellation_event.wait()

    async def fake_run(*_args, **_kwargs):
        return None

    async def fake_post(endpoint, _body, **_kwargs):
        posted_endpoints.append(endpoint)
        return {}

    monkeypatch.setattr(validator_main.config, "MODE", "validator")
    monkeypatch.setattr(validator_main.config, "SIMULATE_EVALUATION_RUNS", False)
    monkeypatch.setattr(validator_main.config, "MAX_CONCURRENT_EVALUATION_RUNS", 1)
    monkeypatch.setattr(validator_main, "_poll_evaluation_cancellation", fake_poll)
    monkeypatch.setattr(validator_main, "_run_evaluation_run_with_semaphore", fake_run)
    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)
    monkeypatch.setattr(validator_main, "prune_docker_disk_resources", lambda: None)

    await validator_main._run_evaluation(response)

    assert posted_endpoints == ["/validator/finish-evaluation"]
