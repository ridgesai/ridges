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
    monkeypatch.setattr(validator_main.os, "_exit", lambda code: pytest.fail(f"unexpected os._exit({code})"))

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


class _FakeExit(Exception):
    """Raised by the os._exit stand-in so the test can observe the exit without dying."""


@pytest.mark.anyio
@pytest.mark.parametrize("ack_raises", [False, True])
async def test_run_evaluation_acknowledges_then_exits_when_cleanup_hangs(monkeypatch, ack_raises) -> None:
    response = _request_response(run_count=1)
    started, entered, release, finished, ack = (asyncio.Event() for _ in range(5))
    runs: list[asyncio.Task] = []
    posts: list[str] = []
    exits: list[int] = []

    async def fake_run(*_args, **_kwargs):
        runs.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            entered.set()
            try:
                await release.wait()
            finally:
                finished.set()

    async def fake_poll(_evaluation_id, _agent_id, cancellation_event, cancellation_reason):
        await started.wait()
        cancellation_reason["reason"] = "score bound"
        cancellation_event.set()

    async def fake_post(endpoint, _body, **_kwargs):
        posts.append(endpoint)
        if endpoint == "/validator/cancel-current-evaluation":
            ack.set()
            if ack_raises:
                raise RuntimeError("acknowledgement failed")
        return {}

    def fake_exit(code):
        # Real os._exit never returns, so ordering must be checked here: the platform must
        # already have been acknowledged when the exit is requested.
        assert ack.is_set()
        exits.append(code)
        raise _FakeExit()

    monkeypatch.setattr(validator_main.config, "MODE", "validator")
    monkeypatch.setattr(validator_main.config, "SIMULATE_EVALUATION_RUNS", False)
    monkeypatch.setattr(validator_main.config, "MAX_CONCURRENT_EVALUATION_RUNS", 1)
    monkeypatch.setattr(validator_main.config, "RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setattr(validator_main, "CANCELLATION_CLEANUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(validator_main, "_poll_evaluation_cancellation", fake_poll)
    monkeypatch.setattr(validator_main, "_run_evaluation_run_with_semaphore", fake_run)
    monkeypatch.setattr(validator_main, "post_ridges_platform", fake_post)
    monkeypatch.setattr(validator_main, "prune_docker_disk_resources", lambda: None)
    monkeypatch.setattr(validator_main.os, "_exit", fake_exit)

    evaluation = asyncio.create_task(validator_main._run_evaluation(response))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(ack.wait(), 1)

        # The platform was acknowledged while the run's cleanup was still blocked ...
        assert not finished.is_set()
        assert len(runs) == 1 and not runs[0].done()
        assert posts == ["/validator/cancel-current-evaluation"]

        # ... and the process exit follows the acknowledgement, even when the acknowledgement raised.
        with pytest.raises(_FakeExit):
            await asyncio.wait_for(asyncio.shield(evaluation), 1)
        assert exits == [1]
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(evaluation, *runs, return_exceptions=True), 1)
        assert finished.is_set()
