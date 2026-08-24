from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.config as config
import queries.competition as competition_queries
import utils.database as _db
from api.endpoints import admin as admin_endpoint
from api.endpoints.admin import router as admin_router
from models.competition import (
    CompetitionAdminSnapshot,
    CompetitionAllocationSnapshot,
    CompetitionAllocationUpdateRequest,
    CompetitionPolicy,
    CompetitionPolicyUpdateRequest,
    CompetitionState,
    CompetitionStateUpdateRequest,
)
from queries.competition import (
    replace_competition_allocations,
    replace_competition_policy,
    update_competition_state,
)
from queries.errors import CompetitionAdminConflictError

pytestmark = pytest.mark.anyio

ADMIN_ACTOR = admin_endpoint.COMPETITION_ADMIN_ACTOR


def _policy(**overrides) -> CompetitionPolicy:
    values = {
        "scoring_mode": "consensus",
        "screener_1_threshold": 0.41,
        "screener_2_threshold": 0.42,
        "prune_threshold": 0.43,
        "required_validator_count": 3,
        "pre_screening_enabled": True,
        "auto_approval_enabled": False,
        "hardcoding_policy_version": "hardcoding-v1",
        "incentive_enabled": False,
        "incentive_performance_threshold": 0.03,
        "incentive_cost_threshold": 0.06,
        "incentive_reward_half_life_hours": 336.0,
        "incentive_time_multiplier_scale_hours": 12.0,
    }
    values.update(overrides)
    return CompetitionPolicy(**values)


async def _seed_competition(
    conn,
    *,
    set_id: int,
    groups: tuple[str, ...] = ("screener_1", "screener_2", "validator"),
    started: bool = False,
    policy: CompetitionPolicy | None = None,
    paused: bool = False,
    closed_at: datetime | None = None,
    emissions_end_at: datetime | None = None,
    ended: bool = False,
    weight: Decimal = Decimal("0"),
) -> None:
    if groups:
        await conn.executemany(
            """
            INSERT INTO evaluation_sets (set_id, set_group, problem_name)
            VALUES ($1, $2::evaluationsetgroup, $3)
            """,
            [(set_id, group, f"{group}-problem") for group in groups],
        )
    else:
        await conn.execute("INSERT INTO competitions (set_id) VALUES ($1)", set_id)

    policy_values = (
        {column: None for column in CompetitionPolicy.model_fields} if policy is None else policy.model_dump()
    )
    await conn.execute(
        """
        UPDATE competitions
        SET
            start_date = CASE WHEN $2 THEN clock_timestamp() - INTERVAL '1 day' ELSE NULL END,
            submissions_closed_at = $3,
            is_paused = $4,
            emissions_end_at = $5,
            end_date = CASE WHEN $6 THEN clock_timestamp() ELSE NULL END,
            raw_emission_weight = $7,
            scoring_mode = $8,
            screener_1_threshold = $9,
            screener_2_threshold = $10,
            prune_threshold = $11,
            required_validator_count = $12,
            pre_screening_enabled = $13,
            auto_approval_enabled = $14,
            hardcoding_policy_version = $15,
            incentive_enabled = $16,
            incentive_performance_threshold = $17,
            incentive_cost_threshold = $18,
            incentive_reward_half_life_hours = $19,
            incentive_time_multiplier_scale_hours = $20
        WHERE set_id = $1
        """,
        set_id,
        started,
        closed_at,
        paused,
        emissions_end_at,
        ended,
        weight,
        *(policy_values[column] for column in CompetitionPolicy.model_fields),
    )


@pytest.fixture
async def clean_competitions(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE competition_admin_events, evaluation_sets, competitions, agents RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE competition_admin_events, evaluation_sets, competitions, agents RESTART IDENTITY CASCADE"
        )


def _state_request(**overrides) -> CompetitionStateUpdateRequest:
    values = {
        "started": True,
        "submissions_closed": False,
        "is_paused": False,
        "emissions_end_at": None,
        "ended": False,
        "reason": "operator transition",
    }
    values.update(overrides)
    return CompetitionStateUpdateRequest(**values)


def _policy_request(**overrides) -> CompetitionPolicyUpdateRequest:
    reason = overrides.pop("reason", "operator policy update")
    return CompetitionPolicyUpdateRequest(**_policy(**overrides).model_dump(), reason=reason)


async def test_open_initializes_defaults_once_and_exact_retry_is_not_audited(clean_competitions) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=10)

    opened = await update_competition_state(
        set_id=10,
        target=_state_request(),
        actor=ADMIN_ACTOR,
    )
    retried = await update_competition_state(
        set_id=10,
        target=_state_request(reason="transport retry"),
        actor=ADMIN_ACTOR,
    )

    assert opened.state is CompetitionState.open
    assert opened.start_date is not None
    assert opened.policy == competition_queries.current_competition_policy_defaults(10)
    assert retried == opened
    async with _db.pool.acquire() as conn:
        events = await conn.fetch("SELECT * FROM competition_admin_events")
    assert len(events) == 1
    assert events[0]["operation"] == "state"
    assert events[0]["actor"] == ADMIN_ACTOR
    assert events[0]["reason"] == "operator transition"
    assert json.loads(events[0]["before_state"])["policy"] is None
    assert json.loads(events[0]["after_state"])["policy"]["scoring_mode"] == "consensus"


@pytest.mark.parametrize("missing_group", ["screener_1", "screener_2", "validator"])
async def test_open_requires_a_nonempty_task_in_every_pipeline_group(
    clean_competitions,
    missing_group: str,
) -> None:
    groups = tuple(group for group in ("screener_1", "screener_2", "validator") if group != missing_group)
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=11, groups=groups)

    with pytest.raises(CompetitionAdminConflictError, match=missing_group):
        await update_competition_state(set_id=11, target=_state_request(), actor=ADMIN_ACTOR)

    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT start_date, scoring_mode FROM competitions WHERE set_id = 11")
        assert dict(row) == {"start_date": None, "scoring_mode": None}
        assert await conn.fetchval("SELECT count(*) FROM competition_admin_events") == 0


async def test_open_preserves_explicit_policy_and_cutoff_can_be_reopened_or_amended(
    clean_competitions,
) -> None:
    explicit_policy = _policy(screener_1_threshold=0.87)
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=12, policy=explicit_policy)

    opened = await update_competition_state(set_id=12, target=_state_request(), actor=ADMIN_ACTOR)
    cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
    closed = await update_competition_state(
        set_id=12,
        target=_state_request(submissions_closed=True, emissions_end_at=cutoff),
        actor=ADMIN_ACTOR,
    )
    reopened = await update_competition_state(
        set_id=12,
        target=_state_request(submissions_closed=False, emissions_end_at=None),
        actor=ADMIN_ACTOR,
    )
    old_close = datetime.now(timezone.utc) - timedelta(days=2)
    old_cutoff = old_close + timedelta(hours=1)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE competitions
            SET submissions_closed_at = $2, emissions_end_at = $3
            WHERE set_id = $1
            """,
            12,
            old_close,
            old_cutoff,
        )
    amended = await update_competition_state(
        set_id=12,
        target=_state_request(
            submissions_closed=True,
            emissions_end_at=old_close,
            reason="amend expired cutoff",
        ),
        actor=ADMIN_ACTOR,
    )

    assert opened.policy == explicit_policy
    assert closed.state is CompetitionState.draining
    assert closed.submissions_closed_at is not None
    assert closed.emissions_end_at == cutoff
    assert reopened.state is CompetitionState.open
    assert reopened.submissions_closed_at is None
    assert reopened.emissions_end_at is None
    assert amended.state is CompetitionState.draining
    assert amended.emissions_end_at == amended.submissions_closed_at == old_close


async def test_draft_cancellation_direct_end_and_terminal_state(clean_competitions) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=20, groups=())
        await _seed_competition(conn, set_id=21, started=True, policy=_policy())

    cancelled = await update_competition_state(
        set_id=20,
        target=_state_request(started=False, ended=True),
        actor=ADMIN_ACTOR,
    )
    ended = await update_competition_state(
        set_id=21,
        target=_state_request(ended=True),
        actor=ADMIN_ACTOR,
    )
    assert cancelled.state is CompetitionState.ended
    assert cancelled.start_date is None
    assert ended.state is CompetitionState.ended
    assert ended.end_date is not None

    with pytest.raises(CompetitionAdminConflictError, match="terminal"):
        await update_competition_state(
            set_id=21,
            target=_state_request(ended=True, is_paused=True),
            actor=ADMIN_ACTOR,
        )
    with pytest.raises(CompetitionAdminConflictError, match="return to draft"):
        async with _db.pool.acquire() as conn:
            await _seed_competition(conn, set_id=22, started=True, policy=_policy())
        await update_competition_state(
            set_id=22,
            target=_state_request(started=False),
            actor=ADMIN_ACTOR,
        )


async def test_policy_replace_is_complete_editable_after_end_and_noop_safe(clean_competitions) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=30, started=True, ended=True, policy=_policy())

    changed = await replace_competition_policy(
        set_id=30,
        target=_policy_request(screener_1_threshold=0.87, hardcoding_policy_version="hardcoding-v2"),
        actor=ADMIN_ACTOR,
    )
    retried = await replace_competition_policy(
        set_id=30,
        target=_policy_request(
            screener_1_threshold=0.87,
            hardcoding_policy_version="hardcoding-v2",
            reason="ignored",
        ),
        actor=ADMIN_ACTOR,
    )

    assert changed.policy.screener_1_threshold == pytest.approx(0.87)
    assert changed.policy.hardcoding_policy_version == "hardcoding-v2"
    assert retried == changed
    async with _db.pool.acquire() as conn:
        events = await conn.fetch("SELECT operation, before_state, after_state FROM competition_admin_events")
    assert len(events) == 1
    assert events[0]["operation"] == "policy"
    assert json.loads(events[0]["before_state"])["policy"]["screener_1_threshold"] == 0.41
    assert json.loads(events[0]["after_state"])["policy"]["screener_1_threshold"] == 0.87


async def test_allocation_replaces_complete_sorted_vector_and_reports_owner_remainder(clean_competitions) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=40, started=True, ended=True, policy=_policy())
        await _seed_competition(conn, set_id=10, policy=None)

    request = CompetitionAllocationUpdateRequest.model_validate(
        {
            "allocations": [
                {"set_id": 40, "raw_emission_weight": Decimal("0.15")},
                {"set_id": 10, "raw_emission_weight": Decimal("0.25")},
            ],
            "reason": "stage next vector",
        }
    )
    changed = await replace_competition_allocations(target=request, actor=ADMIN_ACTOR)
    retried = await replace_competition_allocations(
        target=request.model_copy(update={"reason": "transport retry"}),
        actor=ADMIN_ACTOR,
    )

    assert [(item.set_id, item.raw_emission_weight) for item in changed.allocations] == [
        (10, Decimal("0.25")),
        (40, Decimal("0.15")),
    ]
    assert changed.owner_emission_weight == Decimal("0.60")
    assert retried == changed
    async with _db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT set_id, raw_emission_weight FROM competitions ORDER BY set_id")
        events = await conn.fetch("SELECT before_state, after_state FROM competition_admin_events")
    assert [(row["set_id"], row["raw_emission_weight"]) for row in rows] == [
        (10, Decimal("0.25")),
        (40, Decimal("0.15")),
    ]
    assert len(events) == 1
    after_state = json.loads(events[0]["after_state"])
    assert [item["set_id"] for item in after_state["allocations"]] == [10, 40]
    assert after_state["owner_emission_weight"] == "0.60"


async def test_allocation_rejects_stale_catalog_and_model_rejects_invalid_vectors(clean_competitions) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=50)
        await _seed_competition(conn, set_id=51)

    incomplete = CompetitionAllocationUpdateRequest(
        allocations=[{"set_id": 50, "raw_emission_weight": Decimal("0.5")}],
        reason="stale vector",
    )
    with pytest.raises(CompetitionAdminConflictError, match=r"missing=\[51\]"):
        await replace_competition_allocations(target=incomplete, actor=ADMIN_ACTOR)

    invalid_vectors = [
        [{"set_id": 50, "raw_emission_weight": Decimal("0.5")}] * 2,
        [
            {"set_id": 50, "raw_emission_weight": Decimal("0.6")},
            {"set_id": 51, "raw_emission_weight": Decimal("0.5")},
        ],
        [
            {"set_id": 50, "raw_emission_weight": Decimal("1")},
            {"set_id": 51, "raw_emission_weight": Decimal("1e-400")},
        ],
        [{"set_id": 50, "raw_emission_weight": Decimal("NaN")}],
        [{"set_id": 50, "raw_emission_weight": Decimal("-0.1")}],
    ]
    for allocations in invalid_vectors:
        with pytest.raises(ValidationError):
            CompetitionAllocationUpdateRequest(allocations=allocations, reason="invalid")


def test_allocation_snapshot_preserves_a_microscopic_owner_remainder() -> None:
    main_share = Decimal("0." + ("9" * 400))

    snapshot = competition_queries._allocation_snapshot({1: main_share})

    assert snapshot.owner_emission_weight == Decimal("1e-400")


async def test_audit_failure_rolls_back_state_change(clean_competitions, monkeypatch) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=60, started=True, policy=_policy())

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(competition_queries, "_insert_competition_admin_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await update_competition_state(
            set_id=60,
            target=_state_request(is_paused=True),
            actor=ADMIN_ACTOR,
        )

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT is_paused FROM competitions WHERE set_id = 60") is False
        assert await conn.fetchval("SELECT count(*) FROM competition_admin_events") == 0


def _sample_snapshot() -> CompetitionAdminSnapshot:
    return CompetitionAdminSnapshot(
        set_id=1,
        state=CompetitionState.open,
        started=True,
        start_date=datetime.now(timezone.utc),
        submissions_closed=False,
        submissions_closed_at=None,
        is_paused=False,
        emissions_end_at=None,
        ended=False,
        end_date=None,
        raw_emission_weight=Decimal("0"),
        policy=_policy(),
    )


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(admin_endpoint, "update_competition_state", AsyncMock(return_value=_sample_snapshot()))
    monkeypatch.setattr(admin_endpoint, "replace_competition_policy", AsyncMock(return_value=_sample_snapshot()))
    monkeypatch.setattr(
        admin_endpoint,
        "replace_competition_allocations",
        AsyncMock(
            return_value=CompetitionAllocationSnapshot(
                allocations=[],
                owner_emission_weight=Decimal("1"),
            )
        ),
    )
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    return TestClient(app)


def test_competition_routes_require_configured_admin_bearer(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    state_payload = _state_request().model_dump(mode="json")
    policy_payload = _policy_request().model_dump(mode="json")
    allocation_payload = {"allocations": [], "reason": "set vector"}
    routes = [
        ("/admin/competitions/1/state", state_payload),
        ("/admin/competitions/1/policy", policy_payload),
        ("/admin/competition-allocations", allocation_payload),
    ]
    for path, payload in routes:
        assert client.put(path, json=payload).status_code == 401
        assert client.put(path, json=payload, headers={"Authorization": "Bearer wrong"}).status_code == 401

    monkeypatch.setattr(config, "COLDKEY_BAN_ADMIN_API_KEY", None)
    for path, payload in routes:
        assert client.put(path, json=payload).status_code == 503


def test_competition_routes_accept_strict_complete_bodies_and_fixed_actor(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    auth = {"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"}

    state_payload = _state_request(reason="  open it  ").model_dump(mode="json")
    assert client.put("/admin/competitions/1/state", json=state_payload, headers=auth).status_code == 200
    state_call = admin_endpoint.update_competition_state.await_args
    assert state_call.kwargs["actor"] == ADMIN_ACTOR
    assert state_call.kwargs["target"].reason == "open it"

    policy_payload = _policy_request().model_dump(mode="json")
    assert client.put("/admin/competitions/1/policy", json=policy_payload, headers=auth).status_code == 200
    assert admin_endpoint.replace_competition_policy.await_args.kwargs["actor"] == ADMIN_ACTOR

    allocation_payload = {
        "allocations": [{"set_id": 1, "raw_emission_weight": 0.25}],
        "reason": "set vector",
    }
    assert client.put("/admin/competition-allocations", json=allocation_payload, headers=auth).status_code == 200
    assert admin_endpoint.replace_competition_allocations.await_args.kwargs["actor"] == ADMIN_ACTOR

    for path, payload in (
        ("/admin/competitions/1/state", {**state_payload, "unexpected": True}),
        ("/admin/competitions/1/policy", {**policy_payload, "unexpected": True}),
        ("/admin/competition-allocations", {**allocation_payload, "unexpected": True}),
    ):
        assert client.put(path, json=payload, headers=auth).status_code == 422

    assert (
        client.put(
            "/admin/competitions/1/state",
            json={**state_payload, "reason": "   "},
            headers=auth,
        ).status_code
        == 422
    )
