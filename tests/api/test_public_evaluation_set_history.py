from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
import api.endpoints.retrieval as retrieval_endpoint
import api.endpoints.statistics as statistics_endpoint
import utils.database as _db
from api.endpoints import competitions as competitions_endpoint
from queries.competition import get_public_evaluation_set_context
from queries.evaluation_set import get_evaluation_set_score_distribution
from utils.ttl import clear_all_ttl_caches

pytestmark = pytest.mark.anyio

CREATED_AT = datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def clean_public_history_tables(postgres_db):
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_runs, evaluations, evaluation_sets, agent_scores, agents, "
            "competitions, benchmark_agent_ids, banned_coldkeys RESTART IDENTITY CASCADE"
        )
    yield
    clear_all_ttl_caches()


async def _insert_evaluation_set(conn, set_id: int, *, created_at: datetime) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
        VALUES ($1, 'validator', $2, $3)
        """,
        set_id,
        f"problem-{set_id}",
        created_at,
    )


async def _insert_missing_competition_history(conn, set_id: int) -> None:
    async with conn.transaction():
        await conn.execute("ALTER TABLE evaluation_sets DISABLE TRIGGER ALL")
        try:
            await _insert_evaluation_set(conn, set_id, created_at=CREATED_AT)
        finally:
            await conn.execute("ALTER TABLE evaluation_sets ENABLE TRIGGER ALL")


async def _insert_null_start_history(conn, set_id: int) -> None:
    await _insert_evaluation_set(conn, set_id, created_at=CREATED_AT + timedelta(days=1))
    agent_id = uuid4()
    evaluation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
        ) VALUES ($1, $2, $2, 1, 'evaluating', $3, '127.0.0.1', $4)
        """,
        agent_id,
        f"history-{set_id}",
        CREATED_AT + timedelta(days=1, hours=1),
        set_id,
    )
    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id, agent_id, validator_hotkey, set_id, created_at, evaluation_set_group
        ) VALUES ($1, $2, 'validator-history', $3, $4, 'validator')
        """,
        evaluation_id,
        agent_id,
        set_id,
        CREATED_AT + timedelta(days=1, hours=2),
    )
    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id, evaluation_id, problem_name, status, created_at,
            started_running_agent_at, finished_or_errored_at, verifier_reward, cost_usd
        ) VALUES ($1, $2, $3, 'finished', $4, $4, $4, 1, 0.1)
        """,
        uuid4(),
        evaluation_id,
        f"problem-{set_id}",
        CREATED_AT + timedelta(days=1, hours=3),
    )
    await conn.execute(
        "UPDATE competitions SET created_at = $2 WHERE set_id = $1",
        set_id,
        CREATED_AT + timedelta(days=30),
    )


async def _insert_null_start_nonhistory(
    conn,
    set_id: int,
    *,
    competition_created_at: datetime,
    evaluation_created_at: datetime,
    end_date: datetime | None = None,
) -> None:
    await _insert_evaluation_set(conn, set_id, created_at=competition_created_at)
    await conn.execute(
        "UPDATE competitions SET created_at = $2, end_date = $3 WHERE set_id = $1",
        set_id,
        competition_created_at,
        end_date,
    )
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
        ) VALUES ($1, $2, $2, 1, 'evaluating', $3, '127.0.0.1', $4)
        """,
        agent_id,
        f"private-{set_id}",
        evaluation_created_at,
        set_id,
    )
    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id, agent_id, validator_hotkey, set_id, created_at, evaluation_set_group
        ) VALUES ($1, $2, 'validator-private', $3, $4, 'validator')
        """,
        uuid4(),
        agent_id,
        set_id,
        evaluation_created_at,
    )


async def _seed_history_shapes() -> None:
    async with _db.pool.acquire() as conn:
        await _insert_missing_competition_history(conn, 7)
        await _insert_null_start_history(conn, 55)
        await _insert_evaluation_set(conn, 56, created_at=CREATED_AT + timedelta(days=2))
        await conn.execute(
            "UPDATE competitions SET start_date = $2 WHERE set_id = $1",
            56,
            CREATED_AT + timedelta(days=2),
        )
        await _insert_evaluation_set(conn, 57, created_at=CREATED_AT + timedelta(days=3))
        equal_created_at = CREATED_AT + timedelta(days=40)
        await _insert_null_start_nonhistory(
            conn,
            58,
            competition_created_at=equal_created_at,
            evaluation_created_at=equal_created_at,
        )
        post_competition_created_at = CREATED_AT + timedelta(days=50)
        await _insert_null_start_nonhistory(
            conn,
            59,
            competition_created_at=post_competition_created_at,
            evaluation_created_at=post_competition_created_at + timedelta(seconds=1),
        )
        cancelled_created_at = CREATED_AT + timedelta(days=60)
        await _insert_null_start_nonhistory(
            conn,
            60,
            competition_created_at=cancelled_created_at,
            evaluation_created_at=cancelled_created_at + timedelta(seconds=1),
            end_date=cancelled_created_at + timedelta(days=1),
        )


async def test_history_classifier_index_and_strict_competition_catalog() -> None:
    await _seed_history_shapes()

    missing_row = await get_public_evaluation_set_context(7)
    persisted_null_start = await get_public_evaluation_set_context(55)
    opened = await get_public_evaluation_set_context(56)

    assert missing_row is not None
    assert missing_row.grandfathered_history is True
    assert missing_row.required_validator_count == 1
    assert missing_row.use_historical_cache is True
    assert persisted_null_start is not None
    assert persisted_null_start.grandfathered_history is True
    assert opened is not None
    assert opened.grandfathered_history is False
    assert opened.required_validator_count is None
    assert await get_public_evaluation_set_context(57) is None
    assert await get_public_evaluation_set_context(58) is None
    assert await get_public_evaluation_set_context(59) is None
    assert await get_public_evaluation_set_context(60) is None
    assert await get_public_evaluation_set_context(999) is None

    legacy_distribution = await get_evaluation_set_score_distribution(
        55,
        persisted_null_start.required_validator_count,
    )
    incomplete_opened_distribution = await get_evaluation_set_score_distribution(
        55,
        opened.required_validator_count,
    )
    assert [(row["stage"], row["bucket_index"], row["agents"]) for row in legacy_distribution] == [("validator", 9, 1)]
    assert incomplete_opened_distribution == []

    evaluation_sets = await evaluation_sets_endpoint.evaluation_sets_list()
    assert [evaluation_set.id for evaluation_set in evaluation_sets] == [7, 55, 56]
    assert [competition.set_id for competition in await competitions_endpoint.competition_catalog()] == [56]

    with pytest.raises(HTTPException) as legacy_competition:
        await competitions_endpoint.competition_detail(7)
    assert legacy_competition.value.status_code == 404
    with pytest.raises(HTTPException) as empty_draft:
        await evaluation_sets_endpoint.resolve_explicit_set_id(57)
    assert empty_draft.value.status_code == 404


async def test_grandfathered_routes_use_history_and_validator_fallback(monkeypatch) -> None:
    await _seed_history_shapes()
    overview_calls: list[tuple[str, int, int | None]] = []
    problem_stats_calls: list[tuple[str, int]] = []

    async def past_overview(set_id: int, required_validator_count: int | None):
        overview_calls.append(("past", set_id, required_validator_count))
        return object()

    async def live_overview(set_id: int, required_validator_count: int | None):
        overview_calls.append(("live", set_id, required_validator_count))
        return object()

    async def past_problem_stats(set_id: int):
        problem_stats_calls.append(("past", set_id))
        return object()

    async def live_problem_stats(set_id: int):
        problem_stats_calls.append(("live", set_id))
        return object()

    monkeypatch.setattr(evaluation_sets_endpoint, "_cached_build_past_overview", past_overview)
    monkeypatch.setattr(evaluation_sets_endpoint, "_cached_build_live_overview", live_overview)
    monkeypatch.setattr(statistics_endpoint, "_cached_past_problem_statistics", past_problem_stats)
    monkeypatch.setattr(statistics_endpoint, "_cached_live_problem_statistics", live_problem_stats)

    problems = await evaluation_sets_endpoint.evaluation_set_problems(7)
    assert [problem.problem_name for problem in problems] == ["problem-7"]
    assert (await evaluation_sets_endpoint.evaluation_set_detail(7)).id == 7
    assert await evaluation_sets_endpoint.evaluation_set_overview(7) is not None
    assert await evaluation_sets_endpoint.evaluation_set_leaderboard(7) == []
    assert await evaluation_sets_endpoint.evaluation_set_approved_agents(7) == []
    assert await statistics_endpoint.problem_statistics(set_id=7) is not None

    assert await evaluation_sets_endpoint.evaluation_set_overview(56) is not None
    assert await statistics_endpoint.problem_statistics(set_id=56) is not None
    assert overview_calls == [("past", 7, 1), ("live", 56, None)]
    assert problem_stats_calls == [("past", 7), ("live", 56)]


async def test_agent_versions_validate_context_and_pin_legacy_evidence() -> None:
    await _seed_history_shapes()
    modern_agent_id = uuid4()
    legacy_agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE competitions
            SET scoring_mode = 'consensus',
                screener_1_threshold = 0.4,
                screener_2_threshold = 0.4,
                prune_threshold = 0.4,
                required_validator_count = 1,
                pre_screening_enabled = FALSE,
                auto_approval_enabled = FALSE,
                hardcoding_policy_version = 'hardcoding-v1',
                incentive_enabled = FALSE,
                incentive_performance_threshold = 0.03,
                incentive_cost_threshold = 0.06,
                incentive_reward_half_life_hours = 336,
                incentive_time_multiplier_scale_hours = 12
            WHERE set_id = 56
            """
        )
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
            ) VALUES ($1, 'shared-history-miner', 'modern', 2, 'finished', $2, '127.0.0.1', 56)
            """,
            modern_agent_id,
            CREATED_AT + timedelta(days=2, hours=1),
        )
        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE evaluations DISABLE TRIGGER ALL")
            try:
                await conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, miner_hotkey, name, version_num, status,
                        created_at, ip_address, set_id
                    ) VALUES ($1, 'shared-history-miner', 'legacy', 1, 'finished', $2, '127.0.0.1', NULL)
                    """,
                    legacy_agent_id,
                    CREATED_AT + timedelta(hours=1),
                )
                await conn.execute(
                    """
                    INSERT INTO evaluations (
                        evaluation_id, agent_id, validator_hotkey, set_id,
                        created_at, evaluation_set_group
                    ) VALUES ($1, $2, 'legacy-validator', 7, $3, 'validator')
                    """,
                    uuid4(),
                    legacy_agent_id,
                    CREATED_AT + timedelta(hours=2),
                )
            finally:
                await conn.execute("ALTER TABLE evaluations ENABLE TRIGGER ALL")
                await conn.execute("ALTER TABLE agents ENABLE TRIGGER ALL")

    global_versions = await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner")
    legacy_versions = await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner", set_id=7)
    modern_versions = await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner", set_id=56)
    compatible_versions = await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner", set_id=-1)

    assert {agent.agent_id for agent in global_versions} == {modern_agent_id, legacy_agent_id}
    assert [agent.agent_id for agent in legacy_versions] == [legacy_agent_id]
    assert legacy_versions[0].legacy_membership is True
    assert legacy_versions[0].competition_state is not None
    assert legacy_versions[0].competition_state.set_id == 7
    assert [agent.agent_id for agent in modern_versions] == [modern_agent_id]
    assert [agent.agent_id for agent in compatible_versions] == [modern_agent_id]

    with pytest.raises(HTTPException) as draft:
        await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner", set_id=57)
    assert draft.value.status_code == 404
    with pytest.raises(HTTPException) as unknown:
        await retrieval_endpoint.all_agents_by_hotkey("shared-history-miner", set_id=999)
    assert unknown.value.status_code == 404
