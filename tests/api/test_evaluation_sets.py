from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
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
            "TRUNCATE evaluation_sets, agents, agent_scores, evaluations, approved_agents, competitions, benchmark_agent_ids RESTART IDENTITY CASCADE"
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


async def _insert_evaluation(conn, *, agent_id, set_id: int, set_group: str) -> None:
    await conn.execute(
        """INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at, evaluation_set_group)
           VALUES ($1, $2, $3, $4, NOW(), $5)""",
        uuid4(),
        agent_id,
        "validator-hotkey",
        set_id,
        set_group,
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
        await _insert_evaluations(
            conn,
            agent_id=agent_a,
            set_id=2,
            set_groups=["screener_1", "screener_2", "validator"],
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
    assert result.created_at == SET_2_CREATED

    # Submission stats
    assert result.submissions.total_agents == 3
    assert result.submissions.unique_miners == 3
    assert result.submissions.hardcoded_rejection_rate == pytest.approx(1 / 3, rel=1e-3)

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


@pytest.mark.anyio
async def test_evaluation_set_detail_returns_404_for_unknown_set():
    with pytest.raises(HTTPException) as exc_info:
        await evaluation_sets_endpoint.evaluation_set_detail(set_id=999)
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


@pytest.mark.anyio
async def test_evaluation_set_approved_agents_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        evaluation_sets_endpoint.subtensor_client,
        "get_emission",
        AsyncMock(return_value=0.0),
    )
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)
    assert result == []


@pytest.mark.anyio
async def test_evaluation_set_approved_agents_returns_approved_agents(monkeypatch):
    monkeypatch.setattr(
        evaluation_sets_endpoint.subtensor_client,
        "get_emission",
        AsyncMock(return_value=0.005),
    )
    agent_id_a = uuid4()
    agent_id_b = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id_a,
            miner_hotkey="hotkey-a",
            status="finished",
            created_at=AGENT_TS_SET_1,
        )
        await _insert_agent(
            conn,
            agent_id=agent_id_b,
            miner_hotkey="hotkey-b",
            status="finished",
            created_at=AGENT_TS_SET_1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id_a, set_id=1)
        await _insert_approved_agent(conn, agent_id=agent_id_b, set_id=1)
        await _insert_agent_score(conn, agent_id=agent_id_a, miner_hotkey="hotkey-a", set_id=1, final_score=90.0)
        await _insert_agent_score(conn, agent_id=agent_id_b, miner_hotkey="hotkey-b", set_id=1, final_score=70.0)

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert len(result) == 2
    # Ordered by final_score DESC
    assert result[0].miner_hotkey == "hotkey-a"
    assert result[0].final_score == 90.0
    assert result[0].emission == 0.005
    assert result[0].id == agent_id_a
    assert result[1].miner_hotkey == "hotkey-b"
    assert result[1].final_score == 70.0


@pytest.mark.anyio
async def test_evaluation_set_approved_agents_emission_defaults_to_zero_on_subtensor_error(
    monkeypatch,
):
    monkeypatch.setattr(
        evaluation_sets_endpoint.subtensor_client,
        "get_emission",
        AsyncMock(side_effect=RuntimeError("subtensor down")),
    )
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1, created_at=SET_1_CREATED)
        await _insert_agent(
            conn,
            agent_id=agent_id,
            miner_hotkey="hotkey-a",
            status="finished",
            created_at=AGENT_TS_SET_1,
        )
        await _insert_approved_agent(conn, agent_id=agent_id, set_id=1)
        await _insert_agent_score(conn, agent_id=agent_id, miner_hotkey="hotkey-a", set_id=1, final_score=80.0)

    result = await evaluation_sets_endpoint.evaluation_set_approved_agents(set_id=1)

    assert len(result) == 1
    assert result[0].emission == 0.0
    assert result[0].final_score == 80.0


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

    result = await evaluation_sets_endpoint.evaluation_set_detail(set_id=-1)

    assert result.id == 2
