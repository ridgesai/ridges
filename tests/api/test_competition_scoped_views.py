from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import api.endpoints.retrieval as retrieval_endpoint
import api.endpoints.scoring as scoring_endpoint
import utils.database as _db
from models.competition import CompetitionPolicy
from models.evaluation_set import EvaluationSetGroup
from models.queue import QueueStage
from queries.statistics import (
    get_average_score_per_evaluation_set_group,
    get_average_wait_time_per_evaluation_set_group,
)
from utils.incentives import calculate_time_multiplier
from utils.ttl import clear_all_ttl_caches

pytestmark = pytest.mark.anyio

OBSERVED_AT = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _policy(*, threshold: float, validator_count: int, scale_hours: float) -> CompetitionPolicy:
    return CompetitionPolicy(
        scoring_mode="consensus",
        screener_1_threshold=threshold,
        screener_2_threshold=threshold + 0.01,
        prune_threshold=threshold + 0.02,
        required_validator_count=validator_count,
        pre_screening_enabled=True,
        auto_approval_enabled=False,
        hardcoding_policy_version=f"hardcoding-v{validator_count}",
        incentive_enabled=True,
        incentive_performance_threshold=threshold + 0.03,
        incentive_cost_threshold=threshold + 0.04,
        incentive_reward_half_life_hours=336,
        incentive_time_multiplier_scale_hours=scale_hours,
    )


async def _insert_competition(
    conn,
    *,
    set_id: int,
    policy: CompetitionPolicy | None,
    ended: bool = False,
) -> None:
    values = {field: None for field in CompetitionPolicy.model_fields} if policy is None else policy.model_dump()
    await conn.execute(
        """
        INSERT INTO competitions (
            set_id, created_at, start_date, end_date,
            scoring_mode, screener_1_threshold, screener_2_threshold,
            prune_threshold, required_validator_count, pre_screening_enabled,
            auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
            incentive_performance_threshold, incentive_cost_threshold,
            incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
        ) VALUES (
            $1, $2::timestamptz, $2::timestamptz,
            CASE WHEN $3 THEN $2::timestamptz ELSE NULL::timestamptz END,
            $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
        )
        """,
        set_id,
        OBSERVED_AT - timedelta(days=set_id),
        ended,
        *(values[field] for field in CompetitionPolicy.model_fields),
    )


@pytest.fixture(autouse=True)
async def clean_public_view_tables(postgres_db):
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_sets, agent_scores, agents, competitions, benchmark_agent_ids, "
            "banned_coldkeys RESTART IDENTITY CASCADE"
        )
    yield
    clear_all_ttl_caches()


async def test_screener_info_uses_each_exact_stored_policy_and_keeps_missing_policy_nullable(monkeypatch):
    score_calls: list[int] = []
    wait_calls: list[tuple[int, int | None]] = []
    empty_values = {group: None for group in EvaluationSetGroup}

    async def average_scores(set_id: int):
        score_calls.append(set_id)
        return empty_values

    async def average_waits(set_id: int, validator_count: int | None):
        wait_calls.append((set_id, validator_count))
        return empty_values

    monkeypatch.setattr(scoring_endpoint, "get_average_score_per_evaluation_set_group", average_scores)
    monkeypatch.setattr(scoring_endpoint, "get_average_wait_time_per_evaluation_set_group", average_waits)

    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, policy=_policy(threshold=0.11, validator_count=2, scale_hours=5))
        await _insert_competition(conn, set_id=2, policy=_policy(threshold=0.21, validator_count=4, scale_hours=9))
        await _insert_competition(conn, set_id=3, policy=None)

    first = await scoring_endpoint.screener_info(set_id=1)
    second = await scoring_endpoint.screener_info(set_id=2)
    missing = await scoring_endpoint.screener_info(set_id=3)

    assert first.set_id == 1
    assert first.screener_1_threshold == pytest.approx(0.11)
    assert first.prune_threshold == pytest.approx(0.13)
    assert second.set_id == 2
    assert second.screener_1_threshold == pytest.approx(0.21)
    assert second.prune_threshold == pytest.approx(0.23)
    assert missing.set_id == 3
    assert missing.screener_1_threshold is None
    assert missing.incentive_performance_threshold is None
    assert score_calls == [1, 2, 3]
    assert wait_calls == [(1, 2), (2, 4), (3, None)]


async def test_unscoped_screener_info_remains_readable_while_only_competition_is_paused(monkeypatch):
    empty_values = {group: None for group in EvaluationSetGroup}

    async def average_scores(set_id: int):
        assert set_id == 1
        return empty_values

    async def average_waits(set_id: int, validator_count: int | None):
        assert (set_id, validator_count) == (1, 2)
        return empty_values

    monkeypatch.setattr(scoring_endpoint, "get_average_score_per_evaluation_set_group", average_scores)
    monkeypatch.setattr(scoring_endpoint, "get_average_wait_time_per_evaluation_set_group", average_waits)

    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, policy=_policy(threshold=0.11, validator_count=2, scale_hours=5))
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")

    result = await scoring_endpoint.screener_info()

    assert result.set_id == 1
    assert result.screener_1_threshold == pytest.approx(0.11)
    assert result.prune_threshold == pytest.approx(0.13)


async def test_network_statistics_uses_exact_policy_count_and_scale_without_global_fallback(monkeypatch):
    leader_calls: list[tuple[int, int]] = []
    approved_at = OBSERVED_AT - timedelta(hours=2)

    async def leader(set_id: int, *, required_validator_count: int):
        leader_calls.append((set_id, required_validator_count))
        return SimpleNamespace(
            final_score=set_id / 10,
            avg_cost_usd=set_id / 100,
            approved_at=approved_at,
            observed_at=OBSERVED_AT,
        )

    monkeypatch.setattr(retrieval_endpoint, "get_approved_leader_ranking_for_set", leader)
    first_policy = _policy(threshold=0.11, validator_count=2, scale_hours=5)
    second_policy = _policy(threshold=0.21, validator_count=4, scale_hours=9)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, policy=first_policy)
        await _insert_competition(conn, set_id=2, policy=second_policy)
        await _insert_competition(conn, set_id=3, policy=None)

    first = await retrieval_endpoint.network_statistics(set_id=1)
    compatible = await retrieval_endpoint.network_statistics()
    missing = await retrieval_endpoint.network_statistics(set_id=3)

    assert (first.set_id, first.perf_threshold, first.cost_threshold) == (1, 0.14, 0.15)
    assert first.time_multiplier == calculate_time_multiplier(elapsed_hours=2, scale_hours=5)
    assert compatible.set_id == 2
    assert compatible.time_multiplier == calculate_time_multiplier(elapsed_hours=2, scale_hours=9)
    assert missing.model_dump() == {
        "set_id": 3,
        "top_score": None,
        "top_cost": None,
        "perf_threshold": None,
        "cost_threshold": None,
        "last_approval": None,
        "time_multiplier": None,
    }
    assert leader_calls == [(1, 2), (2, 4)]


async def test_queue_and_top_agent_queries_isolate_two_open_competitions():
    first_agent_id = uuid4()
    second_agent_id = uuid4()
    mismatched_agent_id = uuid4()
    legacy_agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, policy=_policy(threshold=0.11, validator_count=2, scale_hours=5))
        await _insert_competition(conn, set_id=2, policy=_policy(threshold=0.21, validator_count=4, scale_hours=9))
        await conn.executemany(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status,
                created_at, ip_address, set_id
            ) VALUES ($1, $2, $2, 1, $3, $4, '127.0.0.1', $5)
            """,
            [
                (first_agent_id, "first-hotkey", "pre_screening", OBSERVED_AT - timedelta(hours=2), 1),
                (second_agent_id, "second-hotkey", "pre_screening", OBSERVED_AT - timedelta(hours=1), 2),
                (mismatched_agent_id, "mismatched-hotkey", "finished", OBSERVED_AT - timedelta(hours=1), 2),
            ],
        )
        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER ALL")
            try:
                await conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, miner_hotkey, name, version_num, status,
                        created_at, ip_address, set_id
                    ) VALUES ($1, 'legacy-hotkey', 'legacy-hotkey', 1, 'finished', $2, '127.0.0.1', NULL)
                    """,
                    legacy_agent_id,
                    OBSERVED_AT - timedelta(hours=3),
                )
            finally:
                await conn.execute("ALTER TABLE agents ENABLE TRIGGER ALL")

    first_queue = await retrieval_endpoint._build_queue(QueueStage.pre_screening, 1)
    second_queue = await retrieval_endpoint._build_queue(QueueStage.pre_screening, 2)

    assert [agent.agent_id for agent in first_queue] == [first_agent_id]
    assert [agent.agent_id for agent in second_queue] == [second_agent_id]

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE agents SET status = 'finished'")
        async with conn.transaction():
            await conn.execute("ALTER TABLE agent_scores DISABLE TRIGGER ALL")
            try:
                await conn.executemany(
                    """
                    INSERT INTO agent_scores (
                        agent_id, miner_hotkey, name, version_num, created_at,
                        status, set_id, approved, validator_count, final_score
                    ) VALUES ($1, $2, $2, 1, $3, 'finished', $4, FALSE, 1, $5)
                    """,
                    [
                        (first_agent_id, "first-hotkey", OBSERVED_AT - timedelta(hours=2), 1, 0.4),
                        (second_agent_id, "second-hotkey", OBSERVED_AT - timedelta(hours=1), 2, 0.9),
                        (mismatched_agent_id, "mismatched-hotkey", OBSERVED_AT - timedelta(hours=1), 1, 0.99),
                        (legacy_agent_id, "legacy-hotkey", OBSERVED_AT - timedelta(hours=3), 1, 0.6),
                    ],
                )
            finally:
                await conn.execute("ALTER TABLE agent_scores ENABLE TRIGGER ALL")

    first_top_agents = await retrieval_endpoint._build_top_agents(1)
    assert [agent.agent_id for agent in first_top_agents] == [legacy_agent_id, first_agent_id]
    assert first_top_agents[0].legacy_membership is True
    assert first_top_agents[1].legacy_membership is False


async def test_screener_averages_exclude_assigned_mismatches_but_keep_null_legacy():
    valid_agent_id = uuid4()
    mismatched_agent_id = uuid4()
    legacy_agent_id = uuid4()
    now = datetime.now(timezone.utc)
    agent_created_at = now - timedelta(minutes=10)

    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=1, policy=_policy(threshold=0.11, validator_count=1, scale_hours=5))
        await _insert_competition(conn, set_id=2, policy=_policy(threshold=0.21, validator_count=1, scale_hours=9))
        await conn.executemany(
            """
            INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
            VALUES (1, $1, $2, $3)
            """,
            [
                ("screener_1", "problem-a", now - timedelta(days=1)),
                ("validator", "problem-error", now - timedelta(days=1)),
            ],
        )
        await conn.executemany(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status,
                created_at, ip_address, set_id
            ) VALUES ($1, $2, $2, 1, 'finished', $3, '127.0.0.1', $4)
            """,
            [
                (valid_agent_id, "valid-statistics", agent_created_at, 1),
                (mismatched_agent_id, "mismatched-statistics", agent_created_at, 2),
            ],
        )
        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER ALL")
            try:
                await conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, miner_hotkey, name, version_num, status,
                        created_at, ip_address, set_id
                    ) VALUES ($1, 'legacy-statistics', 'legacy-statistics', 1, 'finished', $2,
                              '127.0.0.1', NULL)
                    """,
                    legacy_agent_id,
                    agent_created_at,
                )
            finally:
                await conn.execute("ALTER TABLE agents ENABLE TRIGGER ALL")

        evaluations: list[tuple[object, object, str, datetime, float]] = []
        for agent_id, label, score, minutes_ago in (
            (valid_agent_id, "valid", 1.0, (8, 7, 6)),
            (legacy_agent_id, "legacy", 0.0, (8, 7, 6)),
            (mismatched_agent_id, "mismatched", 0.0, (0, -5, -10)),
        ):
            for group, finished_minutes_ago in zip(
                ("screener_1", "screener_2", "validator"),
                minutes_ago,
                strict=True,
            ):
                evaluations.append(
                    (
                        uuid4(),
                        agent_id,
                        f"{label}-{group}",
                        now - timedelta(minutes=finished_minutes_ago),
                        score,
                    )
                )

        async with conn.transaction():
            await conn.execute("ALTER TABLE evaluations DISABLE TRIGGER ALL")
            try:
                for evaluation_id, agent_id, validator_hotkey, finished_at, _ in evaluations:
                    group = validator_hotkey.split("-", 1)[1]
                    await conn.execute(
                        """
                        INSERT INTO evaluations (
                            evaluation_id, agent_id, validator_hotkey, set_id,
                            created_at, finished_at, evaluation_set_group
                        ) VALUES ($1, $2, $3, 1, $4, $4, $5)
                        """,
                        evaluation_id,
                        agent_id,
                        validator_hotkey,
                        finished_at,
                        group,
                    )
            finally:
                await conn.execute("ALTER TABLE evaluations ENABLE TRIGGER ALL")

        for evaluation_id, _, _, finished_at, score in evaluations:
            run_id = uuid4()
            await conn.execute(
                """
                INSERT INTO evaluation_runs (
                    evaluation_run_id, evaluation_id, problem_name, status, created_at,
                    started_running_agent_at, finished_or_errored_at, verifier_reward,
                    cost_usd, test_results
                ) VALUES ($1, $2, 'problem-a', 'finished', $3, $3, $3, $4, 0.1,
                          $5::jsonb)
                """,
                run_id,
                evaluation_id,
                finished_at,
                score,
                '[{"name":"public-test","category":"unit","status":"pass"}]',
            )
            await conn.execute(
                """
                INSERT INTO inferences (
                    evaluation_run_id, provider, model, temperature, messages,
                    num_input_tokens, num_output_tokens
                ) VALUES ($1, 'test', 'test-model', 0, '[]'::jsonb, 10, 5)
                """,
                run_id,
            )

        error_evaluations = []
        async with conn.transaction():
            await conn.execute("ALTER TABLE evaluations DISABLE TRIGGER ALL")
            try:
                for agent_id, label in (
                    (valid_agent_id, "valid"),
                    (legacy_agent_id, "legacy"),
                    (mismatched_agent_id, "mismatched"),
                ):
                    evaluation_id = uuid4()
                    error_evaluations.append((evaluation_id, agent_id))
                    await conn.execute(
                        """
                        INSERT INTO evaluations (
                            evaluation_id, agent_id, validator_hotkey, set_id,
                            created_at, finished_at, evaluation_set_group
                        ) VALUES ($1, $2, $3, 1, $4, $4, 'validator')
                        """,
                        evaluation_id,
                        agent_id,
                        f"{label}-error",
                        now,
                    )
            finally:
                await conn.execute("ALTER TABLE evaluations ENABLE TRIGGER ALL")

        for evaluation_id, _ in error_evaluations:
            await conn.execute(
                """
                INSERT INTO evaluation_runs (
                    evaluation_run_id, evaluation_id, problem_name, status, error_code,
                    error_message, created_at, finished_or_errored_at
                ) VALUES ($1, $2, 'problem-error', 'error', 3000, 'restart', $3, $3)
                """,
                uuid4(),
                evaluation_id,
                now,
            )

    scores = await get_average_score_per_evaluation_set_group(1)
    waits = await get_average_wait_time_per_evaluation_set_group(1, 1)
    assert scores[EvaluationSetGroup.screener_1] == pytest.approx(0.5)
    assert scores[EvaluationSetGroup.screener_2] == pytest.approx(0.5)
    assert scores[EvaluationSetGroup.validator] == pytest.approx(0.5)
    assert waits[EvaluationSetGroup.screener_1] == pytest.approx(120)
    assert waits[EvaluationSetGroup.screener_2] == pytest.approx(60)
    assert waits[EvaluationSetGroup.validator] == pytest.approx(60)
