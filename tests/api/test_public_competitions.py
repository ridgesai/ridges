from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException

import utils.database as _db
from api.endpoints import competitions as competitions_endpoint
from models.competition import CompetitionPolicy, CompetitionState
from queries.competition import resolve_compatibility_competition_set_id

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _policy() -> CompetitionPolicy:
    return CompetitionPolicy(
        scoring_mode="consensus",
        screener_1_threshold=0.31,
        screener_2_threshold=0.42,
        prune_threshold=0.53,
        required_validator_count=2,
        pre_screening_enabled=True,
        auto_approval_enabled=False,
        hardcoding_policy_version="hardcoding-v1",
        incentive_enabled=True,
        incentive_performance_threshold=0.03,
        incentive_cost_threshold=0.06,
        incentive_reward_half_life_hours=336.0,
        incentive_time_multiplier_scale_hours=12.0,
    )


async def _insert_competition(
    conn,
    *,
    set_id: int,
    name: str | None,
    start_date: datetime | None = NOW,
    submissions_closed_at: datetime | None = None,
    is_paused: bool = False,
    emissions_end_at: datetime | None = None,
    end_date: datetime | None = None,
    raw_emission_weight: Decimal = Decimal("0"),
    configured: bool = True,
) -> None:
    policy_values = _policy().model_dump() if configured else {field: None for field in CompetitionPolicy.model_fields}
    await conn.execute(
        """
        INSERT INTO competitions (
            set_id,
            name,
            created_at,
            start_date,
            submissions_closed_at,
            is_paused,
            emissions_end_at,
            end_date,
            raw_emission_weight,
            scoring_mode,
            screener_1_threshold,
            screener_2_threshold,
            prune_threshold,
            required_validator_count,
            pre_screening_enabled,
            auto_approval_enabled,
            hardcoding_policy_version,
            incentive_enabled,
            incentive_performance_threshold,
            incentive_cost_threshold,
            incentive_reward_half_life_hours,
            incentive_time_multiplier_scale_hours
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
        )
        """,
        set_id,
        name,
        NOW - timedelta(days=set_id),
        start_date,
        submissions_closed_at,
        is_paused,
        emissions_end_at,
        end_date,
        raw_emission_weight,
        *(policy_values[field] for field in CompetitionPolicy.model_fields),
    )


@pytest.fixture(autouse=True)
async def clean_competitions(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_sets, competitions RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_sets, competitions RESTART IDENTITY CASCADE")


async def test_catalog_n0_and_private_detail_return_no_public_competition() -> None:
    assert await competitions_endpoint.competition_catalog() == []
    assert await competitions_endpoint.competition_catalog(accepting=True) == []
    assert await resolve_compatibility_competition_set_id() is None

    with pytest.raises(HTTPException) as error:
        await competitions_endpoint.competition_detail(1)
    assert error.value.status_code == 404


async def test_catalog_derives_every_state_and_capability_without_global_flags(monkeypatch) -> None:
    future_cutoff = datetime.now(timezone.utc) + timedelta(days=1)
    past_cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, name="Draft", start_date=None, configured=False)
        await _insert_competition(
            conn,
            set_id=2,
            name="Cancelled draft",
            start_date=None,
            end_date=NOW,
            configured=False,
        )
        await _insert_competition(conn, set_id=3, name="Open", raw_emission_weight=Decimal("0.4"))
        await _insert_competition(
            conn,
            set_id=4,
            name="Paused",
            is_paused=True,
            raw_emission_weight=Decimal("0.1"),
        )
        await _insert_competition(
            conn,
            set_id=5,
            name="Draining",
            submissions_closed_at=NOW,
            emissions_end_at=future_cutoff,
            raw_emission_weight=Decimal("0.2"),
        )
        await _insert_competition(
            conn,
            set_id=6,
            name="Post cutoff",
            submissions_closed_at=NOW - timedelta(days=2),
            emissions_end_at=past_cutoff,
            raw_emission_weight=Decimal("0.2"),
        )
        await _insert_competition(
            conn,
            set_id=7,
            name="Ended",
            end_date=NOW,
            raw_emission_weight=Decimal("0.1"),
        )
        await _insert_competition(conn, set_id=8, name=None, configured=False)

    monkeypatch.setattr("api.config.DISALLOW_UPLOADS", True)
    monkeypatch.setattr("api.config.VALIDATORS_PAUSED", True, raising=False)

    catalog = await competitions_endpoint.competition_catalog()
    assert [competition.set_id for competition in catalog] == [8, 7, 6, 5, 4, 3]
    by_id = {competition.set_id: competition for competition in catalog}

    assert by_id[3].state is CompetitionState.open
    assert (by_id[3].accepting, by_id[3].processable, by_id[3].emission_active) == (True, True, True)
    assert by_id[4].state is CompetitionState.paused
    assert (by_id[4].accepting, by_id[4].processable, by_id[4].emission_active) == (False, False, False)
    assert by_id[5].state is CompetitionState.draining
    assert (by_id[5].accepting, by_id[5].processable, by_id[5].emission_active) == (False, True, True)
    assert by_id[6].state is CompetitionState.draining
    assert (by_id[6].accepting, by_id[6].processable, by_id[6].emission_active) == (False, True, False)
    assert by_id[7].state is CompetitionState.ended
    assert (by_id[7].accepting, by_id[7].processable, by_id[7].emission_active) == (False, False, False)
    assert by_id[8].state is CompetitionState.open
    assert (by_id[8].accepting, by_id[8].processable, by_id[8].emission_active) == (False, False, False)

    accepting = await competitions_endpoint.competition_catalog(accepting=True)
    assert [(competition.set_id, competition.name) for competition in accepting] == [(3, "Open")]
    not_accepting = await competitions_endpoint.competition_catalog(accepting=False)
    assert [competition.set_id for competition in not_accepting] == [8, 7, 6, 5, 4]


async def test_compatibility_resolver_prefers_newest_open_then_draining_then_paused() -> None:
    async with _db.pool.acquire() as conn:
        await _insert_competition(
            conn,
            set_id=10,
            name="New draining",
            submissions_closed_at=NOW,
            emissions_end_at=NOW + timedelta(days=1),
        )
        await _insert_competition(conn, set_id=2, name="Old open")
        await _insert_competition(conn, set_id=100, name="Higher draft", start_date=None, configured=False)

    assert await resolve_compatibility_competition_set_id() == 2

    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=11, name="New open")
    assert await resolve_compatibility_competition_set_id() == 11

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id IN (2, 11)")
    assert await resolve_compatibility_competition_set_id() == 10

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET end_date = clock_timestamp() WHERE set_id = 10")
    assert await resolve_compatibility_competition_set_id() == 11

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET end_date = clock_timestamp() WHERE set_id = 11")
    assert await resolve_compatibility_competition_set_id() == 2

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET end_date = clock_timestamp() WHERE set_id = 2")
    assert await resolve_compatibility_competition_set_id() is None


async def test_public_openapi_is_lean_and_explicit_detail_hides_drafts() -> None:
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, name="Visible", raw_emission_weight=Decimal("0.25"))
        await _insert_competition(conn, set_id=2, name="Private", start_date=None, configured=False)

    visible = await competitions_endpoint.competition_detail(1)
    assert visible.name == "Visible"
    assert visible.raw_emission_weight == pytest.approx(0.25)
    with pytest.raises(HTTPException) as private:
        await competitions_endpoint.competition_detail(2)
    assert private.value.status_code == 404

    app = FastAPI()
    app.include_router(competitions_endpoint.router, prefix="/competitions")
    schema = app.openapi()
    assert set(schema["paths"]) == {"/competitions", "/competitions/{set_id}"}
    public_schema = schema["components"]["schemas"]["PublicCompetition"]["properties"]
    assert set(public_schema) == {
        "set_id",
        "name",
        "state",
        "accepting",
        "processable",
        "emission_active",
        "created_at",
        "start_date",
        "submissions_closed_at",
        "emissions_end_at",
        "end_date",
        "raw_emission_weight",
    }
    assert "policy" not in public_schema
