import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
import utils.database as _db
from models.agent import AgentStatus
from queries.evaluation_set import get_evaluation_set_performance_improvement
from utils.ttl import clear_all_ttl_caches


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_sets, agents, agent_scores, evaluations, approved_agents, competitions, "
            "benchmark_agent_ids, banned_coldkeys RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
async def remove_caching(monkeypatch):
    clear_all_ttl_caches()
    monkeypatch.setattr(evaluation_sets_endpoint, "_cached_build_detail", evaluation_sets_endpoint._build_detail)
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_leaderboard", evaluation_sets_endpoint._build_leaderboard
    )
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_approved_agents", evaluation_sets_endpoint._build_approved_agents
    )
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_live_overview", evaluation_sets_endpoint._build_overview
    )
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_past_overview", evaluation_sets_endpoint._build_overview
    )
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_live_problems", evaluation_sets_endpoint._build_problems
    )
    monkeypatch.setattr(
        evaluation_sets_endpoint, "_cached_build_past_problems", evaluation_sets_endpoint._build_problems
    )
    yield
    clear_all_ttl_caches()


SET_1_CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)
SET_2_CREATED = datetime(2026, 5, 22, tzinfo=timezone.utc)
AGENT_TS_SET_2 = datetime(2026, 5, 22, 1, tzinfo=timezone.utc)  # 1 h after SET_2_CREATED
AGENT_TS_SET_1 = datetime(2026, 5, 1, 1, tzinfo=timezone.utc)  # 1 h after SET_1_CREATED


async def _insert_eval_set(conn, set_id: int, created_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1, $2, $3, $4)",
        set_id,
        "screener_1",
        "problem-a",
        created_at,
    )
    await _configure_competition(conn, set_id=set_id, start_date=created_at)


async def _configure_competition(conn, *, set_id: int, start_date: datetime) -> None:
    await conn.execute(
        """
        UPDATE competitions
        SET
            start_date = $2,
            scoring_mode = 'consensus',
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
        WHERE set_id = $1
        """,
        set_id,
        start_date,
    )


async def _insert_agent(
    conn,
    *,
    agent_id,
    miner_hotkey: str,
    status: str,
    created_at: datetime,
    set_id: int | None = None,
    miner_coldkey: str | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO agents (
               agent_id, miner_hotkey, miner_coldkey, name, version_num,
               status, created_at, ip_address, set_id
           )
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        agent_id,
        miner_hotkey,
        miner_coldkey,
        miner_hotkey,
        1,
        status,
        created_at,
        "127.0.0.1",
        set_id,
    )


async def _insert_evaluation(
    conn,
    *,
    agent_id,
    set_id: int,
    set_group: str,
    validator_hotkey: str = "validator-hotkey",
):
    evaluation_id = uuid4()
    await conn.execute(
        """INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at, evaluation_set_group)
           VALUES ($1, $2, $3, $4, NOW(), $5)""",
        evaluation_id,
        agent_id,
        validator_hotkey,
        set_id,
        set_group,
    )
    return evaluation_id


async def _insert_finished_evaluation_run(
    conn,
    *,
    evaluation_id,
    problem_name: str = "problem-a",
    cost_usd: float = 0.1,
    runtime_seconds: int = 60,
    verifier_reward: float = 1.0,
) -> None:
    started_at = datetime(2026, 5, 22, 2, tzinfo=timezone.utc)
    await conn.execute(
        """INSERT INTO evaluation_runs (
               evaluation_run_id,
               evaluation_id,
               problem_name,
               status,
               created_at,
               started_running_agent_at,
               finished_or_errored_at,
               verifier_reward,
               cost_usd
           )
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        uuid4(),
        evaluation_id,
        problem_name,
        "finished",
        started_at,
        started_at,
        started_at + timedelta(seconds=runtime_seconds),
        verifier_reward,
        cost_usd,
    )


async def _insert_scored_evaluation(
    conn,
    *,
    agent_id,
    set_id: int,
    set_group: str,
    solved: int,
    total: int,
    finished_at: datetime,
    cost_usd: float = 0.1,
    validator_hotkey: str = "validator-hotkey",
):
    evaluation_id = await _insert_evaluation(
        conn,
        agent_id=agent_id,
        set_id=set_id,
        set_group=set_group,
        validator_hotkey=validator_hotkey,
    )
    for problem_index in range(total):
        await _insert_finished_evaluation_run(
            conn,
            evaluation_id=evaluation_id,
            problem_name=f"problem-{problem_index}",
            cost_usd=cost_usd,
            verifier_reward=1.0 if problem_index < solved else 0.0,
        )
    await conn.execute(
        "UPDATE evaluations SET finished_at = $2 WHERE evaluation_id = $1",
        evaluation_id,
        finished_at,
    )
    return evaluation_id


async def _insert_evaluations(conn, *, agent_id, set_id: int, set_groups: list[str]) -> None:
    for group in set_groups:
        await _insert_evaluation(conn, agent_id=agent_id, set_id=set_id, set_group=group)


async def _insert_approved_agent(conn, *, agent_id, set_id: int, approved_at: datetime | None = None) -> None:
    if approved_at is not None:
        await conn.execute(
            "INSERT INTO approved_agents (agent_id, set_id, approved_at) VALUES ($1, $2, $3)",
            agent_id,
            set_id,
            approved_at,
        )
    else:
        await conn.execute(
            "INSERT INTO approved_agents (agent_id, set_id) VALUES ($1, $2)",
            agent_id,
            set_id,
        )


async def _insert_agent_score(conn, *, agent_id, miner_hotkey: str, set_id: int, final_score: float) -> None:
    await conn.execute(
        """INSERT INTO agent_scores
               (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, approved, validator_count, final_score)
           VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9)""",
        agent_id,
        miner_hotkey,
        miner_hotkey,
        1,
        "finished",
        set_id,
        True,
        1,
        final_score,
    )


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_all_sets():
    async with _db.pool.acquire() as conn:
        # Multiple rows per set_id — GROUP BY must collapse them into exactly 2 sets
        await conn.executemany(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            [
                (1, "screener_1", "problem-a"),
                (1, "screener_2", "problem-b"),
                (1, "validator", "problem-c"),
                (2, "screener_1", "problem-a"),
                (2, "validator", "problem-b"),
            ],
        )
        await _configure_competition(conn, set_id=1, start_date=SET_1_CREATED)
        await _configure_competition(conn, set_id=2, start_date=SET_2_CREATED)

    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_empty_when_no_sets():
    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert result == []


@pytest.mark.anyio
async def test_evaluation_set_overview_returns_distributions_and_improvement_frontier():
    finished_agent = uuid4()
    screener_rejected_agent = uuid4()
    pre_screening_rejected_agent = uuid4()
    unresolved_agent = uuid4()
    banned_agent = uuid4()
    benchmark_agent = uuid4()
    approved_at = datetime(2026, 5, 23, 4, tzinfo=timezone.utc)

    async with _db.pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO evaluation_sets (
                set_id,
                set_group,
                problem_name,
                created_at
            ) VALUES ($1, $2, $3, $4)
            """,
            [
                (2, set_group, f"problem-{problem_index}", SET_2_CREATED)
                for set_group in ("screener_1", "screener_2", "validator")
                for problem_index in range(4)
            ],
        )
        await _configure_competition(conn, set_id=2, start_date=SET_2_CREATED)

        await _insert_agent(
            conn,
            agent_id=finished_agent,
            miner_hotkey="miner-finished",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=screener_rejected_agent,
            miner_hotkey="miner-screener-rejected",
            status="failed_screening_2",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=1),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=pre_screening_rejected_agent,
            miner_hotkey="miner-pre-screening-rejected",
            status="failed_pre_screening",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=2),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=unresolved_agent,
            miner_hotkey="miner-unresolved",
            status="pre_screening_needs_review",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=3),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=banned_agent,
            miner_hotkey="miner-banned",
            miner_coldkey="banned-coldkey",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=4),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=benchmark_agent,
            miner_hotkey="miner-benchmark",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=5),
            set_id=2,
        )
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, $2)",
            "banned-coldkey",
            "test",
        )
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, $2)",
            benchmark_agent,
            "test benchmark",
        )

        # The later successful screener-1 retry scored higher, but the first
        # successful completion is the score that advanced the agent.
        await _insert_scored_evaluation(
            conn,
            agent_id=finished_agent,
            set_id=2,
            set_group="screener_1",
            solved=1,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=1),
        )
        await _insert_scored_evaluation(
            conn,
            agent_id=finished_agent,
            set_id=2,
            set_group="screener_1",
            solved=3,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=2),
        )
        await _insert_scored_evaluation(
            conn,
            agent_id=screener_rejected_agent,
            set_id=2,
            set_group="screener_1",
            solved=2,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=1),
        )
        await _insert_scored_evaluation(
            conn,
            agent_id=finished_agent,
            set_id=2,
            set_group="screener_2",
            solved=3,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=3),
        )
        await _insert_scored_evaluation(
            conn,
            agent_id=screener_rejected_agent,
            set_id=2,
            set_group="screener_2",
            solved=1,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=3),
        )
        await _insert_scored_evaluation(
            conn,
            agent_id=finished_agent,
            set_id=2,
            set_group="validator",
            solved=3,
            total=4,
            finished_at=AGENT_TS_SET_2 + timedelta(hours=4),
            cost_usd=0.2,
        )

        await _insert_approved_agent(
            conn,
            agent_id=finished_agent,
            set_id=2,
            approved_at=approved_at,
        )
        await _insert_agent_score(
            conn,
            agent_id=finished_agent,
            miner_hotkey="miner-finished",
            set_id=2,
            final_score=0.75,
        )

    result = await evaluation_sets_endpoint.evaluation_set_overview(set_id=2)

    pre_screening = result.performance_distribution.pre_screening
    assert pre_screening.approved == 2
    assert pre_screening.rejected == 1
    assert pre_screening.unresolved == 1

    assert len(result.performance_distribution.screener_1) == 10
    assert len(result.performance_distribution.screener_2) == 10
    assert len(result.performance_distribution.validator) == 10

    assert result.performance_distribution.screener_1[2].agents == 1
    assert result.performance_distribution.screener_1[5].agents == 1
    assert sum(bucket.agents for bucket in result.performance_distribution.screener_1) == 2
    assert result.performance_distribution.screener_2[2].agents == 1
    assert result.performance_distribution.screener_2[7].agents == 1
    assert result.performance_distribution.validator[7].agents == 1

    assert result.performance_distribution.screener_1[2].min_score == 0.2
    assert result.performance_distribution.screener_1[2].max_score == 0.3

    assert len(result.performance_improvement) == 1
    improvement = result.performance_improvement[0]
    assert improvement.date == approved_at
    assert improvement.agent_id == finished_agent
    assert improvement.score == 0.75
    assert improvement.cost == 0.2


@pytest.mark.anyio
async def test_evaluation_set_overview_returns_fixed_empty_buckets():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)

    result = await evaluation_sets_endpoint.evaluation_set_overview(set_id=2)

    assert result.performance_distribution.pre_screening.approved == 0
    assert result.performance_distribution.pre_screening.rejected == 0
    assert result.performance_distribution.pre_screening.unresolved == 0
    assert result.performance_improvement == []

    for buckets in (
        result.performance_distribution.screener_1,
        result.performance_distribution.screener_2,
        result.performance_distribution.validator,
    ):
        assert len(buckets) == 10
        assert sum(bucket.agents for bucket in buckets) == 0
        assert buckets[0].min_score == 0
        assert buckets[-1].max_score == 1


@pytest.mark.anyio
async def test_performance_improvement_binds_snapshot_job_to_agent_and_set():
    mismatched_target_id = uuid4()
    foreign_job_owner_id = uuid4()
    valid_agent_id = uuid4()
    legacy_agent_id = uuid4()
    foreign_job_id = uuid4()
    valid_job_id = uuid4()
    legacy_job_id = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        for agent_id, hotkey, created_at, set_id in (
            (mismatched_target_id, "mismatched-target", AGENT_TS_SET_1, 1),
            (foreign_job_owner_id, "foreign-job-owner", AGENT_TS_SET_2, 2),
            (valid_agent_id, "valid-frontier", AGENT_TS_SET_1 + timedelta(minutes=1), 1),
        ):
            await _insert_agent(
                conn,
                agent_id=agent_id,
                miner_hotkey=hotkey,
                status="finished",
                created_at=created_at,
                set_id=set_id,
            )

        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER ALL")
            try:
                await _insert_agent(
                    conn,
                    agent_id=legacy_agent_id,
                    miner_hotkey="legacy-frontier",
                    status="finished",
                    created_at=AGENT_TS_SET_1 + timedelta(minutes=2),
                    set_id=None,
                )
            finally:
                await conn.execute("ALTER TABLE agents ENABLE TRIGGER ALL")

        await conn.executemany(
            """
            INSERT INTO approval_jobs (
                job_id, agent_id, set_id, status, policy_version, input_snapshot
            ) VALUES ($1, $2, $3, 'completed', 'policy-v1', $4::jsonb)
            """,
            [
                (
                    foreign_job_id,
                    foreign_job_owner_id,
                    2,
                    json.dumps({"evaluation_context": {"final_validator_score": 0.99}}),
                ),
                (
                    valid_job_id,
                    valid_agent_id,
                    1,
                    json.dumps({"evaluation_context": {"final_validator_score": 0.6}}),
                ),
            ],
        )
        async with conn.transaction():
            await conn.execute("ALTER TABLE approved_agents DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE approval_jobs DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE agent_approval_states DISABLE TRIGGER ALL")
            try:
                await conn.executemany(
                    """
                    INSERT INTO approved_agents (agent_id, set_id, approved_at)
                    VALUES ($1, 1, $2)
                    """,
                    [
                        (mismatched_target_id, SET_1_CREATED + timedelta(hours=1)),
                        (valid_agent_id, SET_1_CREATED + timedelta(hours=2)),
                        (legacy_agent_id, SET_1_CREATED + timedelta(hours=3)),
                    ],
                )
                await conn.execute(
                    """
                    INSERT INTO approval_jobs (
                        job_id, agent_id, set_id, status, policy_version, input_snapshot
                    ) VALUES ($1, $2, 1, 'completed', 'policy-v1', $3::jsonb)
                    """,
                    legacy_job_id,
                    legacy_agent_id,
                    json.dumps({"evaluation_context": {"final_validator_score": 0.7}}),
                )
                await conn.executemany(
                    """
                    INSERT INTO agent_approval_states (
                        agent_id, set_id, latest_job_id, processing_status
                    ) VALUES ($1, 1, $2, 'completed')
                    """,
                    [
                        (mismatched_target_id, foreign_job_id),
                        (valid_agent_id, valid_job_id),
                        (legacy_agent_id, legacy_job_id),
                    ],
                )
            finally:
                await conn.execute("ALTER TABLE agent_approval_states ENABLE TRIGGER ALL")
                await conn.execute("ALTER TABLE approval_jobs ENABLE TRIGGER ALL")
                await conn.execute("ALTER TABLE approved_agents ENABLE TRIGGER ALL")

    frontier = await get_evaluation_set_performance_improvement(1)

    assert [(row["agent_id"], row["score"]) for row in frontier] == [
        (valid_agent_id, pytest.approx(0.6)),
        (legacy_agent_id, pytest.approx(0.7)),
    ]


@pytest.mark.anyio
async def test_evaluation_set_detail_happy_path():
    agent_a = uuid4()
    agent_b = uuid4()
    agent_c = uuid4()  # hardcoded rejected
    agent_d = uuid4()  # outside set-2 window (belongs to set 1)
    leaderboard_approved_at = datetime(2026, 5, 23, 4, tzinfo=timezone.utc)

    async with _db.pool.acquire() as conn:
        # Two evaluation sets; set 2 is the target
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)

        # Agents inside set-2 window
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=agent_b,
            miner_hotkey="miner-b",
            status="failed_screening_2",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=agent_c,
            miner_hotkey="miner-c",
            status="failed_pre_screening",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        # Agent outside window (in set-1 window)
        await _insert_agent(
            conn,
            agent_id=agent_d,
            miner_hotkey="miner-d",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )

        # Evaluations for set 2
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="screener_1")
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="screener_2")
        agent_a_validator_eval_id = await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="validator")
        await _insert_finished_evaluation_run(
            conn,
            evaluation_id=agent_a_validator_eval_id,
            cost_usd=0.2,
            runtime_seconds=75,
        )
        await _insert_evaluations(
            conn,
            agent_id=agent_b,
            set_id=2,
            set_groups=["screener_1", "screener_2"],
        )

        # Evaluations for set 1
        await _insert_evaluations(
            conn,
            agent_id=agent_d,
            set_id=1,
            set_groups=["screener_1", "screener_2", "validator"],
        )

        # Approved agent
        await _insert_approved_agent(
            conn,
            agent_id=agent_a,
            set_id=2,
            approved_at=leaderboard_approved_at,
        )
        await _insert_approved_agent(conn, agent_id=agent_d, set_id=1)

        # Scores for set 2 and set 1
        await _insert_agent_score(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            set_id=2,
            final_score=0.8,
        )
        await _insert_agent_score(
            conn,
            agent_id=agent_d,
            miner_hotkey="miner-d",
            set_id=1,
            final_score=0.75,
        )

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)

    # Basic identity
    assert result.id == 2

    # Submission stats
    assert result.submissions.total_agents == 3
    assert result.submissions.unique_miners == 3
    assert result.submissions.hardcoded_rejection_rate == pytest.approx(1 / 3, rel=1e-3)
    assert result.submissions.approved_emission_count == 1

    # Pipeline
    assert [stage.stage for stage in result.submissions.pipeline] == [
        "total",
        "pre_screening",
        "screener_1",
        "screener_2",
        "validator",
        "approved_emission",
    ]
    stages = {s.stage: s for s in result.submissions.pipeline}
    assert stages["total"].count == 3
    assert stages["total"].pass_rate == 1
    assert stages["pre_screening"].count == 2
    assert stages["pre_screening"].pass_rate == pytest.approx(2 / 3, rel=1e-3)
    assert stages["screener_1"].count == 2
    assert stages["screener_1"].pass_rate == pytest.approx(2 / 3, rel=1e-3)
    assert stages["screener_2"].count == 1
    assert stages["screener_2"].pass_rate == pytest.approx(1 / 3, rel=1e-3)
    assert stages["validator"].count == 1
    assert stages["validator"].pass_rate == pytest.approx(1 / 3, rel=1e-3)
    assert stages["approved_emission"].count == 1
    assert stages["approved_emission"].pass_rate == pytest.approx(1 / 3, rel=1e-3)

    # Scores
    assert result.scores.best == 0.8
    assert result.scores.average == 0.8
    thresholds = {t.threshold: t.agents_above for t in result.scores.benchmark_thresholds}
    assert thresholds[50] == 1
    assert thresholds[75] == 1
    assert thresholds[90] == 0

    assert result.vs_previous_set is None

    # Enriched summary payload
    assert result.top_agent is not None
    assert result.top_agent.agent_id == agent_a
    assert result.top_agent.name == "miner-a"
    assert result.top_agent.version_num == 1
    assert result.top_agent.final_score == 0.8

    assert result.efficiency.lowest_average_cost_usd_top_agents is not None
    assert result.efficiency.lowest_average_cost_usd_top_agents.agent_id == agent_a
    assert result.efficiency.lowest_average_cost_usd_top_agents.value == 0.2
    assert result.efficiency.lowest_average_runtime_seconds_top_agents is not None
    assert result.efficiency.lowest_average_runtime_seconds_top_agents.agent_id == agent_a
    assert result.efficiency.lowest_average_runtime_seconds_top_agents.value == 75
    assert result.efficiency.average_agent_cost_usd == 0.2
    assert result.efficiency.average_agent_runtime_seconds == 75

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)
    agents_by_id = {agent.agent_id: agent for agent in leaderboard}
    assert set(agents_by_id) == {agent_a, agent_b, agent_c}
    agent_a_state = agents_by_id[agent_a].competition_state
    assert agent_a_state is not None
    assert agent_a_state.rank == 1
    assert agent_a_state.approved is True
    assert agent_a_state.approved_at == leaderboard_approved_at
    assert agent_a_state.final_score == 0.8
    assert agent_a_state.validator_count == 1
    assert agent_a_state.average_cost_usd == 0.2
    assert agent_a_state.average_runtime_seconds == 75
    assert agent_a_state.validator_hotkeys == ["validator-hotkey"]
    assert agent_a_state.status == "approved"
    assert agent_a_state.set_id == 2
    agent_b_state = agents_by_id[agent_b].competition_state
    assert agent_b_state is not None
    assert agent_b_state.rank is None
    assert agent_b_state.approved_at is None
    assert agent_b_state.final_score is None
    assert agent_b_state.status == "failed_screening_2"
    agent_c_state = agents_by_id[agent_c].competition_state
    assert agent_c_state is not None
    assert agent_c_state.rank is None
    assert agent_c_state.approved_at is None
    assert agent_c_state.final_score is None
    assert agent_c_state.status == "failed_pre_screening"


@pytest.mark.anyio
async def test_evaluation_set_leaderboard_ranks_by_score_cost_then_submission_time():
    high_score_agent = uuid4()
    lower_cost_tie_agent = uuid4()
    higher_cost_tie_agent = uuid4()
    earlier_time_tie_agent = uuid4()
    later_time_tie_agent = uuid4()
    unscored_agent = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)

        await _insert_agent(
            conn,
            agent_id=high_score_agent,
            miner_hotkey="miner-high-score",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=5),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=lower_cost_tie_agent,
            miner_hotkey="miner-lower-cost",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=10),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=higher_cost_tie_agent,
            miner_hotkey="miner-higher-cost",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=1),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=earlier_time_tie_agent,
            miner_hotkey="miner-earlier-time",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=20),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=later_time_tie_agent,
            miner_hotkey="miner-later-time",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=30),
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=unscored_agent,
            miner_hotkey="miner-unscored",
            status="failed_pre_screening",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=40),
            set_id=2,
        )

        cost_and_runtime = {
            high_score_agent: (3.0, 30),
            lower_cost_tie_agent: (1.0, 80),
            higher_cost_tie_agent: (2.0, 20),
            earlier_time_tie_agent: (4.0, 40),
            later_time_tie_agent: (4.0, 10),
        }
        for agent_id, (cost_usd, runtime_seconds) in cost_and_runtime.items():
            evaluation_id = await _insert_evaluation(
                conn,
                agent_id=agent_id,
                set_id=2,
                set_group="validator",
                validator_hotkey=f"validator-{agent_id}",
            )
            await _insert_finished_evaluation_run(
                conn,
                evaluation_id=evaluation_id,
                cost_usd=cost_usd,
                runtime_seconds=runtime_seconds,
            )

        for agent_id, miner_hotkey, final_score in [
            (high_score_agent, "miner-high-score", 0.9),
            (lower_cost_tie_agent, "miner-lower-cost", 0.8),
            (higher_cost_tie_agent, "miner-higher-cost", 0.8),
            (earlier_time_tie_agent, "miner-earlier-time", 0.7),
            (later_time_tie_agent, "miner-later-time", 0.7),
        ]:
            await _insert_agent_score(
                conn,
                agent_id=agent_id,
                miner_hotkey=miner_hotkey,
                set_id=2,
                final_score=final_score,
            )

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)

    ranked_agent_ids = [
        agent.agent_id
        for agent in leaderboard
        if agent.competition_state is not None and agent.competition_state.rank is not None
    ]
    assert ranked_agent_ids == [
        high_score_agent,
        lower_cost_tie_agent,
        higher_cost_tie_agent,
        earlier_time_tie_agent,
        later_time_tie_agent,
    ]

    agents_by_id = {agent.agent_id: agent for agent in leaderboard}
    assert agents_by_id[unscored_agent].competition_state is not None
    assert agents_by_id[unscored_agent].competition_state.rank is None
    assert agents_by_id[unscored_agent].competition_state.final_score is None

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)
    assert result.efficiency.lowest_average_cost_usd_top_agents is not None
    assert result.efficiency.lowest_average_cost_usd_top_agents.agent_id == lower_cost_tie_agent
    assert result.efficiency.lowest_average_cost_usd_top_agents.value == 1.0
    assert result.efficiency.lowest_average_runtime_seconds_top_agents is not None
    assert result.efficiency.lowest_average_runtime_seconds_top_agents.agent_id == later_time_tie_agent
    assert result.efficiency.lowest_average_runtime_seconds_top_agents.value == 10
    assert result.efficiency.average_agent_cost_usd == 2.8
    assert result.efficiency.average_agent_runtime_seconds == 36


@pytest.mark.anyio
async def test_coldkey_ban_disqualifies_competitor_and_removes_approved_output():
    banned_agent = uuid4()
    eligible_agent = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_agent(
            conn,
            agent_id=banned_agent,
            miner_hotkey="banned-hotkey",
            miner_coldkey="banned-coldkey",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_agent(
            conn,
            agent_id=eligible_agent,
            miner_hotkey="eligible-hotkey",
            miner_coldkey="eligible-coldkey",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=1),
            set_id=2,
        )
        await _insert_approved_agent(conn, agent_id=banned_agent, set_id=2)
        await _insert_approved_agent(conn, agent_id=eligible_agent, set_id=2)
        await _insert_agent_score(
            conn,
            agent_id=banned_agent,
            miner_hotkey="banned-hotkey",
            set_id=2,
            final_score=0.9,
        )
        await _insert_agent_score(
            conn,
            agent_id=eligible_agent,
            miner_hotkey="eligible-hotkey",
            set_id=2,
            final_score=0.8,
        )
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ('banned-coldkey', 'test ban')"
        )

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)
    by_id = {agent.agent_id: agent for agent in leaderboard}
    assert by_id[banned_agent].competition_state is not None
    assert by_id[banned_agent].competition_state.disqualified is True
    assert by_id[banned_agent].competition_state.rank is None
    assert by_id[eligible_agent].competition_state is not None
    assert by_id[eligible_agent].competition_state.disqualified is False
    assert by_id[eligible_agent].competition_state.rank == 1

    detail = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)
    assert detail.scores.best == 0.8
    assert detail.submissions.approved_emission_count == 1
    assert detail.top_agent is not None
    assert detail.top_agent.agent_id == eligible_agent

    approved_agents = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=2)
    assert [agent.agent_id for agent in approved_agents] == [eligible_agent]


@pytest.mark.anyio
async def test_evaluation_set_detail_efficiency_uses_all_ranked_agents_not_top_25_only():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)

        for index in range(26):
            agent_id = uuid4()
            miner_hotkey = f"miner-{index:02d}"
            cost_usd = 0.1 if index == 25 else 10.0
            runtime_seconds = 1 if index == 25 else 100
            final_score = 1.0 - (index * 0.01)

            await _insert_agent(
                conn,
                agent_id=agent_id,
                miner_hotkey=miner_hotkey,
                status="finished",
                created_at=AGENT_TS_SET_2 + timedelta(minutes=index),
                set_id=2,
            )
            evaluation_id = await _insert_evaluation(
                conn,
                agent_id=agent_id,
                set_id=2,
                set_group="validator",
                validator_hotkey=f"validator-{index:02d}",
            )
            await _insert_finished_evaluation_run(
                conn,
                evaluation_id=evaluation_id,
                cost_usd=cost_usd,
                runtime_seconds=runtime_seconds,
            )
            await _insert_agent_score(
                conn,
                agent_id=agent_id,
                miner_hotkey=miner_hotkey,
                set_id=2,
                final_score=final_score,
            )

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)
    assert (
        len(
            [
                agent
                for agent in leaderboard
                if agent.competition_state is not None and agent.competition_state.rank is not None
            ]
        )
        == 26
    )

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)
    assert result.efficiency.lowest_average_cost_usd_top_agents is not None
    assert result.efficiency.lowest_average_cost_usd_top_agents.value == 0.1
    assert result.efficiency.lowest_average_runtime_seconds_top_agents is not None
    assert result.efficiency.lowest_average_runtime_seconds_top_agents.value == 1


@pytest.mark.anyio
async def test_evaluation_set_detail_returns_404_for_unknown_set():
    with pytest.raises(HTTPException) as exc_info:
        # Dependency needs to be called directly, because calling endpoints directly bypasses Fast API's dependency injection
        await evaluation_sets_endpoint.resolve_set_id(999)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_evaluation_set_detail_no_previous_set_returns_null_vs_previous():
    agent_a = uuid4()
    agent_b = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=1,
        )
        await _insert_agent(
            conn,
            agent_id=agent_b,
            miner_hotkey="miner-b",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_evaluation(conn, agent_id=agent_a, set_id=1, set_group="screener_1")
        await _insert_evaluation(conn, agent_id=agent_b, set_id=2, set_group="screener_2")
        await _insert_evaluation(conn, agent_id=agent_b, set_id=2, set_group="validator")
        await _insert_agent_score(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            set_id=1,
            final_score=80.0,
        )

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=1)

    assert result.vs_previous_set is None


@pytest.mark.anyio
async def test_evaluation_set_detail_no_scores_returns_null_best_and_average():
    agent_a = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="failed_screening_1",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="screener_1")
        # No agent_scores rows inserted

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)

    assert result.scores.best is None
    assert result.scores.average is None
    assert all(t.agents_above == 0 for t in result.scores.benchmark_thresholds)
    assert result.vs_previous_set is None
    assert result.top_agent is None
    assert result.efficiency.lowest_average_cost_usd_top_agents is None
    assert result.efficiency.lowest_average_runtime_seconds_top_agents is None
    assert result.efficiency.average_agent_cost_usd is None
    assert result.efficiency.average_agent_runtime_seconds is None

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)
    assert len(leaderboard) == 1
    assert leaderboard[0].agent_id == agent_a
    assert leaderboard[0].competition_state is not None
    assert leaderboard[0].competition_state.rank is None
    assert leaderboard[0].competition_state.final_score is None


@pytest.mark.anyio
async def test_evaluation_set_approved_agents_returns_empty_list(monkeypatch):
    async def no_allocations():
        return evaluation_sets_endpoint.CurrentAllocations(hotkey_weights={}, agent_weights={})

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", no_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)
    assert result == []


@pytest.mark.anyio
async def test_evaluation_set_approved_agents_returns_approved_agents(monkeypatch):
    agent_id_a = uuid4()
    agent_id_b = uuid4()
    approved_at_a = datetime(2026, 5, 1, 10, tzinfo=timezone.utc)  # latest approved appears first
    approved_at_b = datetime(2026, 5, 1, 8, tzinfo=timezone.utc)

    async def current_allocations():
        return evaluation_sets_endpoint.CurrentAllocations(
            hotkey_weights={"hotkey-a": 0.7, "hotkey-b": 0.3},
            agent_weights={agent_id_a: 0.7, agent_id_b: 0.3},
        )

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", current_allocations)

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id_a,
            miner_hotkey="hotkey-a",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_agent(
            conn,
            agent_id=agent_id_b,
            miner_hotkey="hotkey-b",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id_a, set_id=1, approved_at=approved_at_a)
        await _insert_approved_agent(conn, agent_id=agent_id_b, set_id=1, approved_at=approved_at_b)
        await conn.execute(
            """
            UPDATE approved_agents
            SET baseline_agent_id = $1,
                performance_delta = $2,
                cost_delta = $3,
                relative_improvement_units = $4,
                time_multiplier = $5,
                initial_reward_score = $6
            WHERE agent_id = $7 AND set_id = $8
            """,
            agent_id_b,
            0.123456,
            0.065432,
            1.234567,
            1.456789,
            2.34567,
            agent_id_a,
            1,
        )
        await conn.execute(
            """
            INSERT INTO agent_approval_states (
                agent_id, set_id, processing_status, system_verdict, published_verdict
            ) VALUES ($1, $2, 'completed', 'approved', 'approved')
            """,
            agent_id_a,
            1,
        )

        # Insert validator evaluations + runs so validator_metrics CTE can compute cost/runtime
        eval_id_a = await _insert_evaluation(conn, agent_id=agent_id_a, set_id=1, set_group="validator")
        await _insert_finished_evaluation_run(conn, evaluation_id=eval_id_a, cost_usd=0.5, runtime_seconds=120)
        eval_id_b = await _insert_evaluation(conn, agent_id=agent_id_b, set_id=1, set_group="validator")
        await _insert_finished_evaluation_run(conn, evaluation_id=eval_id_b, cost_usd=0.3, runtime_seconds=60)

        # Insert agent_scores AFTER evaluations to avoid trigger-based refresh overwriting them
        # (the refresh_agent_scores trigger fires on evaluation INSERT and clears manual scores)
        await _insert_agent_score(conn, agent_id=agent_id_a, miner_hotkey="hotkey-a", set_id=1, final_score=90.0)
        await _insert_agent_score(conn, agent_id=agent_id_b, miner_hotkey="hotkey-b", set_id=1, final_score=70.0)

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert len(result) == 2
    # Ordered by approved_at DESC (agent_a was the latest approved)
    assert result[0].agent_id == agent_id_a
    assert result[0].miner_hotkey == "hotkey-a"
    assert result[0].emission is None
    assert result[0].reward_weight == pytest.approx(0.7)
    first_state = result[0].competition_state
    assert first_state is not None
    assert first_state.final_score == 90.0
    assert first_state.approved_at == approved_at_a
    assert first_state.approval_review_status == "approved"
    assert first_state.performance_delta == 0.123456
    assert first_state.cost_delta == 0.065432
    assert first_state.relative_improvement_units == 1.234567
    assert first_state.time_multiplier == 1.456789
    assert first_state.initial_reward_score == 2.34567
    assert first_state.baseline_agent_id == agent_id_b
    assert first_state.baseline_agent_name == "hotkey-b"
    assert first_state.baseline_agent_version_num == 1
    assert first_state.average_cost_usd == 0.5
    assert first_state.average_runtime_seconds == 120
    assert first_state.status == "approved"
    assert first_state.set_id == 1

    assert result[1].agent_id == agent_id_b
    assert result[1].miner_hotkey == "hotkey-b"
    assert result[1].emission is None
    assert result[1].reward_weight == pytest.approx(0.3)
    second_state = result[1].competition_state
    assert second_state is not None
    assert second_state.final_score == 70.0
    assert second_state.approved_at == approved_at_b
    assert second_state.approval_review_status is None
    assert second_state.performance_delta is None
    assert second_state.cost_delta is None
    assert second_state.relative_improvement_units is None
    assert second_state.time_multiplier is None
    assert second_state.initial_reward_score is None
    assert second_state.baseline_agent_id is None
    assert second_state.baseline_agent_name is None
    assert second_state.baseline_agent_version_num is None
    assert second_state.average_cost_usd == 0.3
    assert second_state.average_runtime_seconds == 60
    assert second_state.status == "approved"
    assert second_state.set_id == 1


@pytest.mark.anyio
async def test_approved_agents_exclude_assigned_mismatches_and_hide_mismatched_baselines(monkeypatch):
    valid_agent_id = uuid4()
    mismatched_agent_id = uuid4()
    mismatched_baseline_id = uuid4()
    legacy_agent_id = uuid4()

    async def current_allocations():
        return evaluation_sets_endpoint.CurrentAllocations(hotkey_weights={}, agent_weights={})

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", current_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        for agent_id, hotkey, set_id in (
            (valid_agent_id, "valid", 1),
            (mismatched_agent_id, "mismatched", 2),
            (mismatched_baseline_id, "wrong-baseline", 2),
        ):
            await _insert_agent(
                conn,
                agent_id=agent_id,
                miner_hotkey=hotkey,
                status="finished",
                created_at=AGENT_TS_SET_1,
                set_id=set_id,
            )

        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE agent_scores DISABLE TRIGGER ALL")
            await conn.execute("ALTER TABLE approved_agents DISABLE TRIGGER ALL")
            try:
                await _insert_agent(
                    conn,
                    agent_id=legacy_agent_id,
                    miner_hotkey="legacy",
                    status="finished",
                    created_at=AGENT_TS_SET_1,
                    set_id=None,
                )
                for agent_id, hotkey, score in (
                    (valid_agent_id, "valid", 0.8),
                    (mismatched_agent_id, "mismatched", 0.99),
                    (legacy_agent_id, "legacy", 0.7),
                ):
                    await _insert_agent_score(
                        conn,
                        agent_id=agent_id,
                        miner_hotkey=hotkey,
                        set_id=1,
                        final_score=score,
                    )
                    await _insert_approved_agent(
                        conn,
                        agent_id=agent_id,
                        set_id=1,
                        approved_at=AGENT_TS_SET_1,
                    )
                await conn.execute(
                    "UPDATE approved_agents SET baseline_agent_id = $1 WHERE agent_id = $2 AND set_id = 1",
                    mismatched_baseline_id,
                    valid_agent_id,
                )
            finally:
                await conn.execute("ALTER TABLE approved_agents ENABLE TRIGGER ALL")
                await conn.execute("ALTER TABLE agent_scores ENABLE TRIGGER ALL")
                await conn.execute("ALTER TABLE agents ENABLE TRIGGER ALL")

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert {agent.agent_id for agent in result} == {valid_agent_id, legacy_agent_id}
    legacy = next(agent for agent in result if agent.agent_id == legacy_agent_id)
    valid = next(agent for agent in result if agent.agent_id == valid_agent_id)
    assert legacy.legacy_membership is True
    assert valid.legacy_membership is False
    assert valid.competition_state is not None
    assert valid.competition_state.baseline_agent_id is None
    assert valid.competition_state.baseline_agent_name is None
    assert valid.competition_state.baseline_agent_version_num is None


@pytest.mark.anyio
async def test_live_approved_agents_show_individual_weights_for_shared_hotkey(monkeypatch):
    first_agent_id = uuid4()
    second_agent_id = uuid4()
    first_agent = evaluation_sets_endpoint.PublicAgent(
        agent_id=first_agent_id,
        miner_hotkey="shared-hotkey",
        name="first-agent",
        version_num=1,
        status=AgentStatus.finished,
        created_at=AGENT_TS_SET_1,
        set_id=1,
        approved=True,
        final_score=0.8,
        approved_at=AGENT_TS_SET_1,
    )
    second_agent = first_agent.model_copy(
        update={
            "agent_id": second_agent_id,
            "name": "second-agent",
            "version_num": 2,
        }
    )

    allocations = evaluation_sets_endpoint.CurrentAllocations(
        hotkey_weights={"shared-hotkey": 1.0},
        agent_weights={first_agent_id: 0.25, second_agent_id: 0.75},
    )

    result = evaluation_sets_endpoint._add_approved_agent_weights(
        [first_agent, second_agent],
        allocations,
    )

    assert [agent.reward_weight for agent in result] == pytest.approx([0.25, 0.75])
    assert [agent.emission for agent in result] == [None, None]


@pytest.mark.anyio
async def test_approved_agent_weights_distinguish_calculation_failure_from_zero_weight():
    agent_id = uuid4()
    agent = evaluation_sets_endpoint.PublicAgent(
        agent_id=agent_id,
        miner_hotkey="hotkey",
        name="agent",
        version_num=1,
        status=AgentStatus.finished,
        created_at=AGENT_TS_SET_1,
        set_id=1,
        approved=True,
        final_score=0.9,
        approved_at=AGENT_TS_SET_1,
    )

    failed = evaluation_sets_endpoint._add_approved_agent_weights([agent], None)
    successful = evaluation_sets_endpoint._add_approved_agent_weights(
        [agent],
        evaluation_sets_endpoint.CurrentAllocations(
            hotkey_weights={"owner": 1.0},
            agent_weights={},
        ),
    )

    assert failed[0].emission is None
    assert failed[0].reward_weight is None
    assert successful[0].emission is None
    assert successful[0].reward_weight == 0


@pytest.mark.anyio
async def test_two_open_competitions_enrich_only_their_approved_agents(monkeypatch):
    first_agent_id = uuid4()
    second_agent_id = uuid4()

    async def current_allocations():
        return evaluation_sets_endpoint.CurrentAllocations(
            hotkey_weights={"first-hotkey": 0.2, "second-hotkey": 0.8},
            agent_weights={first_agent_id: 0.2, second_agent_id: 0.8},
        )

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", current_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_agent(
            conn,
            agent_id=first_agent_id,
            miner_hotkey="first-hotkey",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_agent(
            conn,
            agent_id=second_agent_id,
            miner_hotkey="second-hotkey",
            status="finished",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_approved_agent(conn, agent_id=first_agent_id, set_id=1)
        await _insert_approved_agent(conn, agent_id=second_agent_id, set_id=2)
        await _insert_agent_score(
            conn,
            agent_id=first_agent_id,
            miner_hotkey="first-hotkey",
            set_id=1,
            final_score=0.4,
        )
        await _insert_agent_score(
            conn,
            agent_id=second_agent_id,
            miner_hotkey="second-hotkey",
            set_id=2,
            final_score=0.8,
        )

    first = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)
    second = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=2)

    assert [(agent.agent_id, agent.reward_weight, agent.emission) for agent in first] == [(first_agent_id, 0.2, None)]
    assert [(agent.agent_id, agent.reward_weight, agent.emission) for agent in second] == [(second_agent_id, 0.8, None)]


@pytest.mark.anyio
async def test_ended_approved_agents_skip_allocation_calculation(monkeypatch):
    agent_id = uuid4()

    async def unexpected_allocations():
        pytest.fail("ended competition must not calculate current allocations")

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", unexpected_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id,
            miner_hotkey="ended-hotkey",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id, set_id=1)
        await _insert_agent_score(
            conn,
            agent_id=agent_id,
            miner_hotkey="ended-hotkey",
            set_id=1,
            final_score=0.4,
        )
        await conn.execute("UPDATE competitions SET end_date = NOW() WHERE set_id = 1")

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert [(agent.agent_id, agent.reward_weight, agent.emission) for agent in result] == [(agent_id, None, None)]


@pytest.mark.anyio
async def test_open_approved_agents_report_none_when_allocation_calculation_fails(monkeypatch):
    agent_id = uuid4()

    async def failed_allocations():
        raise RuntimeError("chain unavailable")

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", failed_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id,
            miner_hotkey="open-hotkey",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id, set_id=1)
        await _insert_agent_score(
            conn,
            agent_id=agent_id,
            miner_hotkey="open-hotkey",
            set_id=1,
            final_score=0.4,
        )

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert result[0].reward_weight is None
    assert result[0].emission is None


@pytest.mark.anyio
@pytest.mark.parametrize("lifecycle", ["paused", "post_cutoff"])
async def test_nonended_inactive_competitions_report_authoritative_zero_weight(monkeypatch, lifecycle):
    agent_id = uuid4()
    calls = 0

    async def owner_only_allocations():
        nonlocal calls
        calls += 1
        return evaluation_sets_endpoint.CurrentAllocations(
            hotkey_weights={"owner": 1.0},
            agent_weights={},
        )

    monkeypatch.setattr(evaluation_sets_endpoint, "get_current_allocations", owner_only_allocations)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id,
            miner_hotkey="inactive-hotkey",
            status="finished",
            created_at=AGENT_TS_SET_1,
            set_id=1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id, set_id=1)
        await _insert_agent_score(
            conn,
            agent_id=agent_id,
            miner_hotkey="inactive-hotkey",
            set_id=1,
            final_score=0.4,
        )
        if lifecycle == "paused":
            await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")
        else:
            await conn.execute(
                """
                UPDATE competitions
                SET submissions_closed_at = $1, emissions_end_at = $1
                WHERE set_id = 1
                """,
                SET_1_CREATED,
            )

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert calls == 1
    assert result[0].reward_weight == 0
    assert result[0].emission is None


@pytest.mark.anyio
async def test_overview_cache_classification_uses_fresh_competition_lifecycle(monkeypatch):
    live_result = object()
    ended_result = object()

    async def live_builder(set_id: int, required_validator_count: int | None):
        assert set_id == 1
        assert required_validator_count == 1
        return live_result

    async def ended_builder(set_id: int, required_validator_count: int | None):
        assert set_id == 1
        assert required_validator_count == 1
        return ended_result

    monkeypatch.setattr(evaluation_sets_endpoint, "_cached_build_live_overview", live_builder)
    monkeypatch.setattr(evaluation_sets_endpoint, "_cached_build_past_overview", ended_builder)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)

    assert await evaluation_sets_endpoint.evaluation_set_overview(set_id=1) is live_result

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET end_date = NOW() WHERE set_id = 1")

    assert await evaluation_sets_endpoint.evaluation_set_overview(set_id=1) is ended_result


@pytest.mark.anyio
async def test_problem_routes_use_explicit_or_compatibility_competition_without_draft_fallback():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await conn.execute(
            """
            INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
            VALUES (2, 'validator', 'private-draft-problem', $1)
            """,
            SET_2_CREATED,
        )

    explicit = await evaluation_sets_endpoint.evaluation_set_problems(set_id=1)
    compatibility = await evaluation_sets_endpoint.evaluation_sets_all_latest_set_problems()

    assert [problem.problem_name for problem in explicit] == ["problem-a"]
    assert compatibility == explicit
    with pytest.raises(HTTPException) as draft:
        await evaluation_sets_endpoint.resolve_explicit_set_id(2)
    assert draft.value.status_code == 404
    with pytest.raises(HTTPException) as fallback:
        await evaluation_sets_endpoint.resolve_explicit_set_id(-1)
    assert fallback.value.status_code == 404


@pytest.mark.anyio
async def test_evaluation_set_detail_minus_one_resolves_to_newest_open_competition():
    agent_a = uuid4()

    async with _db.pool.acquire() as conn:
        # Both competitions are open, so compatibility chooses the newest open one.
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="failed_screening_1",
            created_at=AGENT_TS_SET_2,
            set_id=2,
        )
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="screener_1")

    # Dependency needs to be called directly, because calling endpoints directly bypasses Fast API's dependency injection
    resolved = await evaluation_sets_endpoint.resolve_set_id(-1)
    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=resolved)

    assert result.id == 2
    assert result.top_agent is None

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=resolved)
    assert len(leaderboard) == 1
    assert leaderboard[0].agent_id == agent_a
