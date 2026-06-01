from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
import utils.database as _db


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_sets, agents, agent_scores, evaluations, approved_agents, competitions RESTART IDENTITY CASCADE"
        )


SET_1_CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)
SET_2_CREATED = datetime(2026, 5, 22, tzinfo=timezone.utc)
AGENT_TS_SET_2 = datetime(2026, 5, 22, 1, tzinfo=timezone.utc)  # 1 h after SET_2_CREATED
AGENT_TS_SET_1 = datetime(2026, 5, 1, 1, tzinfo=timezone.utc)  # 1 h after SET_1_CREATED


async def _insert_competition(conn, set_id: int, created_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO competitions (set_id, name, start_date, end_date, created_at) VALUES ($1, $2, $3, $4, $5)",
        set_id,
        f"competition-{set_id}",
        created_at,
        created_at + timedelta(days=7),
        created_at,
    )


async def _insert_eval_set(conn, set_id: int, created_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1, $2, $3, $4)",
        set_id,
        "screener_1",
        "problem-a",
        created_at,
    )


async def _insert_agent(conn, *, agent_id, miner_hotkey: str, status: str, created_at: datetime) -> None:
    await conn.execute(
        """INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        agent_id,
        miner_hotkey,
        miner_hotkey,
        1,
        status,
        created_at,
        "127.0.0.1",
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
        1.0,
        cost_usd,
    )


async def _insert_evaluations(conn, *, agent_id, set_id: int, set_groups: list[str]) -> None:
    for group in set_groups:
        await _insert_evaluation(conn, agent_id=agent_id, set_id=set_id, set_group=group)


async def _insert_approved_agent(conn, *, agent_id, set_id: int) -> None:
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

    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_empty_when_no_sets():
    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert result == []


@pytest.mark.anyio
async def test_evaluation_set_detail_happy_path():
    agent_a = uuid4()
    agent_b = uuid4()
    agent_c = uuid4()  # hardcoded rejected
    agent_d = uuid4()  # outside set-2 window (belongs to set 1)

    async with _db.pool.acquire() as conn:
        # Two evaluation sets; set 2 is the target
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_competition(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_competition(conn, set_id=2, created_at=SET_2_CREATED)

        # Agents inside set-2 window
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="finished",
            created_at=AGENT_TS_SET_2,
        )
        await _insert_agent(
            conn,
            agent_id=agent_b,
            miner_hotkey="miner-b",
            status="failed_screening_2",
            created_at=AGENT_TS_SET_2,
        )
        await _insert_agent(
            conn,
            agent_id=agent_c,
            miner_hotkey="miner-c",
            status="failed_pre_screening",
            created_at=AGENT_TS_SET_2,
        )
        # Agent outside window (in set-1 window)
        await _insert_agent(
            conn,
            agent_id=agent_d,
            miner_hotkey="miner-d",
            status="finished",
            created_at=AGENT_TS_SET_1,
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
        await _insert_approved_agent(conn, agent_id=agent_a, set_id=2)
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
    stages = {s.stage: s for s in result.submissions.pipeline}
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

    # vs previous set
    assert result.vs_previous_set is not None
    assert result.vs_previous_set.top_score_delta == "+0.05"
    assert result.vs_previous_set.agents_beating_previous_best == 1

    # Enriched summary payload
    assert result.top_agent is not None
    assert result.top_agent.agent_id == agent_a
    assert result.top_agent.name == "miner-a"
    assert result.top_agent.version_num == 1
    assert result.top_agent.final_score == 0.8

    assert result.efficiency.lowest_average_cost_usd_top_agents == 0.2
    assert result.efficiency.lowest_average_runtime_seconds_top_agents == 75
    assert result.efficiency.average_agent_cost_usd == 0.2
    assert result.efficiency.average_agent_runtime_seconds == 75

    leaderboard = await evaluation_sets_endpoint.evaluation_set_leaderboard(set_id=2)
    agents_by_id = {agent.agent_id: agent for agent in leaderboard}
    assert set(agents_by_id) == {agent_a, agent_b, agent_c}
    assert agents_by_id[agent_a].rank == 1
    assert agents_by_id[agent_a].approved_for_emission is True
    assert agents_by_id[agent_a].final_score == 0.8
    assert agents_by_id[agent_a].validator_count == 1
    assert agents_by_id[agent_a].average_cost_usd == 0.2
    assert agents_by_id[agent_a].average_runtime_seconds == 75
    assert agents_by_id[agent_a].validator_hotkeys == ["validator-hotkey"]
    assert agents_by_id[agent_b].rank is None
    assert agents_by_id[agent_b].final_score is None
    assert agents_by_id[agent_c].rank is None
    assert agents_by_id[agent_c].final_score is None


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
        await _insert_competition(conn, set_id=2, created_at=SET_2_CREATED)

        await _insert_agent(
            conn,
            agent_id=high_score_agent,
            miner_hotkey="miner-high-score",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=5),
        )
        await _insert_agent(
            conn,
            agent_id=lower_cost_tie_agent,
            miner_hotkey="miner-lower-cost",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=10),
        )
        await _insert_agent(
            conn,
            agent_id=higher_cost_tie_agent,
            miner_hotkey="miner-higher-cost",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=1),
        )
        await _insert_agent(
            conn,
            agent_id=earlier_time_tie_agent,
            miner_hotkey="miner-earlier-time",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=20),
        )
        await _insert_agent(
            conn,
            agent_id=later_time_tie_agent,
            miner_hotkey="miner-later-time",
            status="finished",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=30),
        )
        await _insert_agent(
            conn,
            agent_id=unscored_agent,
            miner_hotkey="miner-unscored",
            status="failed_pre_screening",
            created_at=AGENT_TS_SET_2 + timedelta(minutes=40),
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

    ranked_agent_ids = [agent.agent_id for agent in leaderboard if agent.rank is not None]
    assert ranked_agent_ids == [
        high_score_agent,
        lower_cost_tie_agent,
        higher_cost_tie_agent,
        earlier_time_tie_agent,
        later_time_tie_agent,
    ]

    agents_by_id = {agent.agent_id: agent for agent in leaderboard}
    assert agents_by_id[unscored_agent].rank is None
    assert agents_by_id[unscored_agent].final_score is None

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)
    assert result.efficiency.lowest_average_cost_usd_top_agents == 1.0
    assert result.efficiency.lowest_average_runtime_seconds_top_agents == 10
    assert result.efficiency.average_agent_cost_usd == 2.8
    assert result.efficiency.average_agent_runtime_seconds == 36


@pytest.mark.anyio
async def test_evaluation_set_detail_efficiency_uses_all_ranked_agents_not_top_25_only():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_competition(conn, set_id=2, created_at=SET_2_CREATED)

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
    assert len([agent for agent in leaderboard if agent.rank is not None]) == 26

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=2)
    assert result.efficiency.lowest_average_cost_usd_top_agents == 0.1
    assert result.efficiency.lowest_average_runtime_seconds_top_agents == 1


@pytest.mark.anyio
async def test_evaluation_set_detail_returns_404_for_unknown_set():
    with pytest.raises(HTTPException) as exc_info:
        # Dependency needs to be called directly, because calling endpoints directly bypasses Fast API's dependency injection
        await evaluation_sets_endpoint.resolve_set_id(999)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_evaluation_set_detail_no_previous_set_returns_null_vs_previous():
    agent_a = uuid4()

    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="finished",
            created_at=AGENT_TS_SET_2,
        )
        await _insert_evaluation(conn, agent_id=agent_a, set_id=1, set_group="screener_1")
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="screener_2")
        await _insert_evaluation(conn, agent_id=agent_a, set_id=2, set_group="validator")
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
    assert leaderboard[0].rank is None
    assert leaderboard[0].final_score is None


@pytest.mark.anyio
async def test_evaluation_set_detail_minus_one_resolves_to_latest_set():
    agent_a = uuid4()

    async with _db.pool.acquire() as conn:
        # Two sets exist; set 2 is the latest (highest set_id)
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_competition(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_eval_set(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_competition(conn, set_id=2, created_at=SET_2_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_a,
            miner_hotkey="miner-a",
            status="failed_screening_1",
            created_at=AGENT_TS_SET_2,
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
