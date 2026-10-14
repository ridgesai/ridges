from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

import utils.database as db
from models.agent import AgentStatus
from models.validator_scheduling import CompetitionSchedulingUpdateRequest, ValidatorAllowlistUpdateRequest
from queries.agent import EvaluationCandidate, get_evaluation_candidates_for_validator_hotkey
from queries.errors import CompetitionNotFoundError
from queries.evaluation import create_new_evaluation_and_evaluation_runs
from queries.validator_scheduling import (
    get_competition_scheduling,
    get_validator_allowlist,
    set_competition_scheduling,
    set_validator_allowlist,
)
from tests.queries.test_competition_lifecycle_gates import _insert_agent, _seed_competition
from utils.database import DatabaseConnection

pytestmark = pytest.mark.anyio
HOTKEY = "validator-a"


@pytest.fixture(autouse=True)
async def clean(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE competitions, agents, evaluation_sets, evaluations, evaluation_runs, "
            "competition_admin_events, validator_competition_allowlists RESTART IDENTITY CASCADE"
        )
        await conn.execute("UPDATE competition_work_cursors SET last_served_set_id = NULL")


async def seed(*ids, status=AgentStatus.evaluating):
    result = {}
    async with db.pool.acquire() as conn:
        for set_id in ids:
            await _seed_competition(conn, set_id=set_id)
            result[set_id] = await _insert_agent(conn, set_id=set_id, status=status)
    return result


async def mode(set_id, value):
    return await set_competition_scheduling(
        set_id=set_id, target=CompetitionSchedulingUpdateRequest(mode=value, reason="routing test"), actor="admin"
    )


async def allow(ids, hotkey=HOTKEY):
    return await set_validator_allowlist(
        validator_hotkey=hotkey,
        target=ValidatorAllowlistUpdateRequest(allowed_set_ids=ids, reason="routing test"),
        actor="admin",
    )


async def order(hotkey=HOTKEY):
    return [c.set_id for c in (await get_evaluation_candidates_for_validator_hotkey(hotkey)).candidates]


async def claim_next(hotkey):
    batch = await get_evaluation_candidates_for_validator_hotkey(hotkey)
    assert batch.candidates
    candidate = batch.candidates[0]
    assignment = await create_new_evaluation_and_evaluation_runs(candidate, hotkey, batch.observed_last_served_set_id)
    assert assignment is not None
    return candidate.set_id


async def test_defaults_and_least_recently_served_priority_tiers():
    await seed(27, 28, 29, 30)
    assert (await get_competition_scheduling(set_id=29)).mode == "normal"
    assert (await get_validator_allowlist(validator_hotkey=HOTKEY)).allowed_set_ids is None
    assert await order() == [27, 28, 29, 30]
    await mode(29, "prioritized")
    await mode(30, "prioritized")
    assert await order() == [29, 30, 27, 28]
    assert await claim_next("first") == 29
    assert await order() == [30, 29, 27, 28]
    assert await claim_next("second") == 30
    assert await order() == [29, 30, 27, 28]
    await mode(29, "normal")
    await mode(30, "normal")
    assert await order() == [27, 28, 29, 30]


async def test_allowlist_before_priority_and_no_fallback_outside_it():
    await seed(27, 28, 29)
    await mode(28, "prioritized")
    await allow([29])
    assert await order() == [29]
    assert await order("validator-b") == [28, 27, 29]
    await mode(29, "disabled")
    assert await order() == []
    await allow(None)
    assert await order() == [28, 27]
    await allow([])
    assert await order() == []
    # Future competitions remain excluded by an explicit allowlist.
    await seed(30)
    await allow([29])
    assert await order() == []


async def test_priority_falls_back_per_validator_and_when_target_full():
    agents = await seed(28, 29)
    await mode(29, "prioritized")
    first = await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agents[29], 29), HOTKEY, None)
    assert first is not None
    assert await order() == [28]
    assert await order("validator-b") == [29, 28]
    second = await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agents[29], 29), "validator-b", 29)
    assert second is not None
    assert await order("validator-c") == [28]


@pytest.mark.parametrize("state", ["draft", "paused", "ended"])
async def test_priority_and_allowlist_do_not_override_lifecycle(state):
    await seed(28)
    async with db.pool.acquire() as conn:
        await _seed_competition(
            conn, set_id=29, started=state != "draft", paused=state == "paused", ended=state == "ended"
        )
        await _insert_agent(conn, set_id=29, status=AgentStatus.evaluating)
    await mode(29, "prioritized")
    await allow([28, 29])
    assert await order() == [28]


@pytest.mark.parametrize("restriction", ["disabled", "empty", "other"])
async def test_stale_candidate_rechecked_without_advancing_cursor(restriction):
    agents = await seed(28, 29)
    candidate = EvaluationCandidate(agents[29], 29)
    if restriction == "disabled":
        await mode(29, "disabled")
    else:
        await allow([] if restriction == "empty" else [28])
    assert await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None) is None
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations") == 0
        assert await conn.fetchval("SELECT count(*) FROM validator_competition_last_served") == 0
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'validator'")
            is None
        )


@pytest.mark.parametrize(
    "hotkey,status", [("screener-1-1", AgentStatus.screening_1), ("screener-2-1", AgentStatus.screening_2)]
)
async def test_screeners_ignore_validator_controls(hotkey, status):
    agents = await seed(29, status=status)
    await mode(29, "disabled")
    await allow([], hotkey)
    assert await order(hotkey) == [29]
    assert (
        await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agents[29], 29), hotkey, None) is not None
    )


async def test_audit_noops_and_invalid_competition_leave_state_unchanged():
    await seed(28, 29)
    await mode(29, "prioritized")
    await mode(29, "prioritized")
    assert (await allow([29, 28])).allowed_set_ids == [28, 29]
    await allow([28, 29])
    with pytest.raises(CompetitionNotFoundError):
        await allow([28, 999])
    with pytest.raises(CompetitionNotFoundError):
        await mode(999, "disabled")
    assert (await get_validator_allowlist(validator_hotkey=HOTKEY)).allowed_set_ids == [28, 29]
    await allow([])
    await allow(None)
    async with db.pool.acquire() as conn:
        events = await conn.fetch("SELECT * FROM competition_admin_events ORDER BY created_at")
        assert len(events) == 4
        assert all(e["actor"] == "admin" and e["reason"] == "routing test" for e in events)
        assert json.loads(events[-2]["after_state"])["allowed_set_ids"] == []
        assert json.loads(events[-1]["after_state"])["allowed_set_ids"] is None


async def test_controls_leave_emission_inputs_and_existing_runs_intact():
    agents = await seed(28, 29)
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET raw_emission_weight = 1 WHERE set_id = 28")
        before = await conn.fetch(
            "SELECT set_id, start_date, end_date, is_paused, raw_emission_weight FROM competitions ORDER BY set_id"
        )
    assignment = await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agents[28], 28), HOTKEY, None)
    await mode(28, "disabled")
    await mode(29, "prioritized")
    await allow([29])
    async with db.pool.acquire() as conn:
        assert (
            await conn.fetch(
                "SELECT set_id, start_date, end_date, is_paused, raw_emission_weight FROM competitions ORDER BY set_id"
            )
            == before
        )
        assert before[0]["raw_emission_weight"] == Decimal(1)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM evaluation_runs WHERE evaluation_id = $1 AND status = 'pending'",
                assignment.evaluation.evaluation_id,
            )
            == 1
        )


async def wait_for_lock(conn, pid):
    async def blocked():
        while not await conn.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(blocked(), 3)


@pytest.mark.parametrize("control", ["allowlist", "mode"])
async def test_admin_first_blocks_then_rejects_stale_claim(control):
    agents = await seed(29)
    claim = None
    try:
        async with db.pool.acquire() as admin, db.pool.acquire() as worker:
            worker_pid = await worker.fetchval("SELECT pg_backend_pid()")
            async with admin.transaction():
                wrapped = DatabaseConnection(admin, "admin")
                if control == "allowlist":
                    await set_validator_allowlist.__wrapped__(
                        wrapped,
                        validator_hotkey=HOTKEY,
                        target=ValidatorAllowlistUpdateRequest(allowed_set_ids=[], reason="restrict"),
                        actor="admin",
                    )
                else:
                    await set_competition_scheduling.__wrapped__(
                        wrapped,
                        set_id=29,
                        target=CompetitionSchedulingUpdateRequest(mode="disabled", reason="restrict"),
                        actor="admin",
                    )
                claim = asyncio.create_task(
                    create_new_evaluation_and_evaluation_runs.__wrapped__(
                        DatabaseConnection(worker, "claim"), EvaluationCandidate(agents[29], 29), HOTKEY, None
                    )
                )
                await wait_for_lock(admin, worker_pid)
                assert not claim.done()
            assert await asyncio.wait_for(claim, 3) is None
    finally:
        if claim is not None and not claim.done():
            claim.cancel()
            await asyncio.gather(claim, return_exceptions=True)


@pytest.mark.parametrize("control", ["allowlist", "mode"])
async def test_assignment_first_finishes_then_admin_applies(control):
    agents = await seed(29)
    update = None
    try:
        async with db.pool.acquire() as worker, db.pool.acquire() as admin:
            admin_pid = await admin.fetchval("SELECT pg_backend_pid()")
            async with worker.transaction():
                wrapped = DatabaseConnection(worker, "claim")
                token = db._per_context_conn.set(wrapped)
                try:
                    first = await create_new_evaluation_and_evaluation_runs(
                        EvaluationCandidate(agents[29], 29), HOTKEY, None
                    )
                finally:
                    db._per_context_conn.reset(token)
                wrapped_admin = DatabaseConnection(admin, "admin")
                if control == "allowlist":
                    operation = set_validator_allowlist.__wrapped__(
                        wrapped_admin,
                        validator_hotkey=HOTKEY,
                        target=ValidatorAllowlistUpdateRequest(allowed_set_ids=[], reason="restrict"),
                        actor="admin",
                    )
                else:
                    operation = set_competition_scheduling.__wrapped__(
                        wrapped_admin,
                        set_id=29,
                        target=CompetitionSchedulingUpdateRequest(mode="disabled", reason="restrict"),
                        actor="admin",
                    )
                update = asyncio.create_task(operation)
                await wait_for_lock(worker, admin_pid)
                assert first is not None
                assert not update.done()
            await asyncio.wait_for(update, 3)
            assert await order() == []
            assert await worker.fetchval("SELECT count(*) FROM evaluations") == 1
    finally:
        if update is not None and not update.done():
            update.cancel()
            await asyncio.gather(update, return_exceptions=True)


async def test_real_weight_snapshot_stays_on_28_when_validators_only_serve_29():
    from api.incentives import calculate_current_allocations
    from queries.scores import get_weight_calculation_snapshot
    from utils.bittensor import HotkeySubnetInfo

    agents = await seed(28, 29)
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET raw_emission_weight = 1 WHERE set_id = 28")
        await conn.execute(
            """
            INSERT INTO agent_scores
                (agent_id, miner_hotkey, name, version_num, status, set_id, created_at,
                 approved, approved_at, validator_count, final_score)
            VALUES ($1, 'leader-28', 'leader', 1, 'finished', 28, NOW(), true, NOW(), 2, 0.9)
            """,
            agents[28],
        )
    registered = {"leader-28": HotkeySubnetInfo(uid=1, emission=0.0)}
    before = calculate_current_allocations(await get_weight_calculation_snapshot(), registered)
    await mode(28, "disabled")
    await mode(29, "prioritized")
    await allow([29])
    after = calculate_current_allocations(await get_weight_calculation_snapshot(), registered)
    assert after == before
    assert after.hotkey_weights == {"leader-28": 1.0}
    assert await order() == [29]


async def seed_backlog():
    await seed(27, 28, 29)
    async with db.pool.acquire() as conn:
        for set_id in (27, 28, 29):
            for _ in range(8):
                await _insert_agent(conn, set_id=set_id, status=AgentStatus.evaluating)


@pytest.mark.parametrize("prioritized", [False, True])
async def test_restricted_pair_alternates_despite_interleaved_claims_on_29(prioritized):
    await seed_backlog()
    await allow([27, 28])
    await allow([29], "other")
    if prioritized:
        await mode(29, "prioritized")
    served = []
    for _ in range(6):
        assert await claim_next("other") == 29
        served.append(await claim_next(HOTKEY))
    assert served == [27, 28, 27, 28, 27, 28]


async def test_unrestricted_validators_balance_around_single_competition_allowlist():
    await seed_backlog()
    await allow([27], "restricted")
    served = []
    for _ in range(6):
        assert await claim_next("restricted") == 27
        served.append(await claim_next(HOTKEY))
    assert served == [28, 29, 28, 29, 28, 29]


async def test_normal_rotation_and_priority_with_real_claims():
    await seed_backlog()
    assert [await claim_next(HOTKEY) for _ in range(6)] == [27, 28, 29, 27, 28, 29]
    await mode(29, "prioritized")
    assert [await claim_next(HOTKEY) for _ in range(4)] == [29, 29, 29, 29]


async def claim_on(conn, candidate, hotkey, observed=None):
    token = db._per_context_conn.set(DatabaseConnection(conn, "overlapping claim"))
    try:
        return await create_new_evaluation_and_evaluation_runs(candidate, hotkey, observed)
    finally:
        db._per_context_conn.reset(token)


@pytest.mark.parametrize("validator_first", [False, True])
async def test_screener_and_validator_claims_do_not_block_or_skip_each_other(validator_first):
    agents = await seed(29)
    async with db.pool.acquire() as conn:
        screener_agent = await _insert_agent(conn, set_id=29, status=AgentStatus.screening_1)
    validator = (EvaluationCandidate(agents[29], 29), HOTKEY)
    screener = (EvaluationCandidate(screener_agent, 29), "screener-1-1")
    first, second = (validator, screener) if validator_first else (screener, validator)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            assert await claim_on(conn, *first) is not None
            # The first claim's competition share lock and timestamp write (if
            # any) stay held while the second claim uses another connection.
            assert await asyncio.wait_for(create_new_evaluation_and_evaluation_runs(*second, None), 2) is not None
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations") == 2
        assert await conn.fetchval("SELECT count(*) FROM validator_competition_last_served") == 1


@pytest.mark.parametrize("previously_served", [False, True])
async def test_timestamp_and_evaluation_roll_back_together(monkeypatch, previously_served):
    from unittest.mock import AsyncMock

    import queries.evaluation as evaluation_queries

    agents = await seed(29)
    async with db.pool.acquire() as conn:
        if previously_served:
            await conn.execute("INSERT INTO validator_competition_last_served VALUES (29, '2026-01-01'::timestamptz)")
        before = await conn.fetchval("SELECT last_served_at FROM validator_competition_last_served WHERE set_id = 29")
    # This read occurs after the timestamp upsert but before the claim commits.
    monkeypatch.setattr(evaluation_queries, "get_evaluation_by_id", AsyncMock(side_effect=RuntimeError("abort claim")))
    with pytest.raises(RuntimeError, match="abort claim"):
        await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agents[29], 29), HOTKEY, None)
    async with db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations") == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluation_runs") == 0
        assert (
            await conn.fetchval("SELECT last_served_at FROM validator_competition_last_served WHERE set_id = 29")
            == before
        )
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'validator'")
            is None
        )


async def test_timestamp_is_actual_service_time_after_waiting_for_cursor():
    agents = await seed(29)
    task = None
    try:
        async with db.pool.acquire() as worker, db.pool.acquire() as blocker:
            async with worker.transaction():
                started = await worker.fetchval("SELECT transaction_timestamp()")
                pid = await worker.fetchval("SELECT pg_backend_pid()")
                async with blocker.transaction():
                    await blocker.execute(
                        "SELECT 1 FROM competition_work_cursors WHERE family = 'validator' FOR UPDATE"
                    )
                    task = asyncio.create_task(claim_on(worker, EvaluationCandidate(agents[29], 29), HOTKEY))
                    await wait_for_lock(blocker, pid)
                    released_after = await blocker.fetchval("SELECT clock_timestamp()")
                assert await asyncio.wait_for(task, 3) is not None
                stamped = await worker.fetchval(
                    "SELECT last_served_at FROM validator_competition_last_served WHERE set_id = 29"
                )
                assert stamped >= released_after > started
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
