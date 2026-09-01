"""Tests for GET /retrieval/agents-by-coldkey."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
import utils.database as _db
from api.endpoints import retrieval as retrieval_module
from queries.competition import initialize_current_competition_policy
from tests.api.test_evaluation_sets import (
    _configure_competition,
    _insert_agent_score,
    _insert_scored_evaluation,
)
from utils.ttl import clear_all_ttl_caches

pytestmark = pytest.mark.anyio

COLDKEY = "5FConsoleColdkey1"
OTHER_COLDKEY = "5FConsoleColdkey2"
HOTKEY_A = "5FConsoleHotkeyA1"
HOTKEY_B = "5FConsoleHotkeyB1"

BASE_TIME = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

_TRUNCATE = (
    "TRUNCATE agents, competitions, agent_scores, evaluations, evaluation_runs, "
    "evaluation_sets, approved_agents, banned_coldkeys, benchmark_agent_ids, "
    "agent_approval_states, unapproved_agent_ids RESTART IDENTITY CASCADE"
)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute(_TRUNCATE)
        await conn.execute("INSERT INTO competitions (set_id, start_date) VALUES (1, NOW())")
    await initialize_current_competition_policy()
    yield
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute(_TRUNCATE)


async def _insert_agent(
    hotkey: str,
    coldkey: str | None,
    name: str,
    version_num: int = 0,
    created_at: datetime | None = None,
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, miner_coldkey, name, version_num,
                status, created_at, ip_address, set_id
            )
            VALUES ($1, $2, $3, $4, $5, 'screening_1', $6, '127.0.0.1', 1)
            """,
            agent_id,
            hotkey,
            coldkey,
            name,
            version_num,
            created_at or (BASE_TIME + timedelta(minutes=version_num)),
        )
    return agent_id


async def test_groups_agents_by_hotkey():
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 0)
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)
    await _insert_agent(HOTKEY_B, COLDKEY, "agent-b", 0)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert list(result.keys()) == [HOTKEY_A, HOTKEY_B]  # hotkeys sorted
    assert [a.version_num for a in result[HOTKEY_A]] == [1, 0]  # newest first
    assert all(a.miner_hotkey == HOTKEY_A for a in result[HOTKEY_A])
    assert all(a.miner_coldkey == COLDKEY for a in result[HOTKEY_A])
    assert len(result[HOTKEY_B]) == 1


async def test_null_coldkey_rows_are_excluded():
    await _insert_agent(HOTKEY_A, None, "dev-agent")

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert result == {}


async def test_unknown_coldkey_returns_empty_dict():
    result = await retrieval_module.agents_by_coldkey(miner_coldkey="5FNobody")
    assert result == {}


async def test_legacy_null_rows_on_owned_hotkey_are_excluded():
    # Agents uploaded before miner_coldkey existed (2026-07-10) carry NULL
    await _insert_agent(HOTKEY_A, None, "agent-a", 0, created_at=BASE_TIME - timedelta(days=60))
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert [a.version_num for a in result[HOTKEY_A]] == [1]


async def test_foreign_coldkey_rows_on_shared_hotkey_are_excluded():
    await _insert_agent(HOTKEY_A, OTHER_COLDKEY, "agent-a", 0)
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a", 1)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert [a.version_num for a in result[HOTKEY_A]] == [1]


async def test_payload_is_public_agent_shape():
    await _insert_agent(HOTKEY_A, COLDKEY, "agent-a")

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    agent = result[HOTKEY_A][0]
    payload = agent.model_dump()
    assert "agent_id" in payload and "status" in payload
    assert "ip_address" not in payload


async def test_route_serializes_and_requires_miner_coldkey(monkeypatch):
    clear_all_ttl_caches()
    row = {
        "agent_id": uuid.uuid4(),
        "miner_hotkey": HOTKEY_A,
        "name": "agent-a",
        "version_num": 0,
        "status": "screening_1",
        "created_at": BASE_TIME,
    }
    monkeypatch.setattr(
        retrieval_module,
        "get_public_agent_rows_by_miner_coldkey",
        AsyncMock(return_value=[row]),
    )
    app = FastAPI()
    app.include_router(retrieval_module.router, prefix="/retrieval")
    client = TestClient(app)

    assert client.get("/retrieval/agents-by-coldkey").status_code == 422  # param required

    response = client.get("/retrieval/agents-by-coldkey", params={"miner_coldkey": COLDKEY})
    assert response.status_code == 200
    body = response.json()
    assert list(body.keys()) == [HOTKEY_A]
    assert body[HOTKEY_A][0]["name"] == "agent-a"
    assert "ip_address" not in body[HOTKEY_A][0]
    clear_all_ttl_caches()


# --- rank / validator-metric enrichment -------------------------------------
#
# The endpoint ranks each agent inside its own competition set by reusing the
# leaderboard's own SQL, so these tests pin both the values and the fact that they
# agree with /evaluation-sets/{set_id}/leaderboard.

SET_1_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
SET_2_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
VALIDATOR_A = "5FValidatorA"
VALIDATOR_B = "5FValidatorB"


async def _setup_competition(conn, set_id: int, start: datetime, *, end: datetime | None = None) -> None:
    """One competition with two validator problems, requiring 2 validators."""
    for group, problem in (("validator", "problem-0"), ("validator", "problem-1")):
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1,$2,$3,$4)",
            set_id,
            group,
            problem,
            start,
        )
    await _configure_competition(conn, set_id=set_id, start_date=start)
    await conn.execute("UPDATE competitions SET required_validator_count = 2 WHERE set_id = $1", set_id)
    if end is not None:
        await conn.execute("UPDATE competitions SET end_date = $1 WHERE set_id = $2", end, set_id)


async def _insert_agent_in_set(
    conn, *, hotkey: str, coldkey: str, name: str, set_id: int, created_at: datetime, status: str = "finished"
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    await conn.execute(
        """INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num,
                               status, created_at, ip_address, set_id)
           VALUES ($1,$2,$3,$4,1,$5,$6,'127.0.0.1',$7)""",
        agent_id,
        hotkey,
        coldkey,
        name,
        status,
        created_at,
        set_id,
    )
    return agent_id


async def _run_validator_evaluations(
    conn, *, agent_id, set_id: int, solved: int, cost_usd: float, runtime_seconds: int
) -> None:
    """Two validator evaluations, with a per-agent runtime so ORDER BY has no ties."""
    for validator_hotkey in (VALIDATOR_A, VALIDATOR_B):
        evaluation_id = await _insert_scored_evaluation(
            conn,
            agent_id=agent_id,
            set_id=set_id,
            set_group="validator",
            solved=solved,
            total=2,
            finished_at=BASE_TIME,
            cost_usd=cost_usd,
            validator_hotkey=validator_hotkey,
        )
        await conn.execute(
            """UPDATE evaluation_runs
               SET finished_or_errored_at = started_running_agent_at + ($2 || ' seconds')::interval
               WHERE evaluation_id = $1""",
            evaluation_id,
            str(runtime_seconds),
        )


def _metrics(agent):
    return (
        agent.rank,
        agent.final_score,
        agent.validator_count,
        agent.average_cost_usd,
        agent.average_runtime_seconds,
        sorted(agent.validator_hotkeys or []),
    )


async def test_enriched_fields_match_leaderboard_and_rank_globally():
    """Enriched fields equal the leaderboard's, and rank counts every agent in the set.

    Two foreign agents outrank ours, so a rank computed over this coldkey's own agents
    would come back 1 instead of 3.
    """
    async with _db.pool.acquire() as conn:
        await _setup_competition(conn, 1, SET_1_START)
        for index, (score, cost) in enumerate(((1.0, 0.05), (0.9, 0.06))):
            rival = await _insert_agent_in_set(
                conn,
                hotkey=f"5FRival{index}",
                coldkey=OTHER_COLDKEY,
                name=f"rival-{index}",
                set_id=1,
                created_at=BASE_TIME,
            )
            await _run_validator_evaluations(
                conn, agent_id=rival, set_id=1, solved=2, cost_usd=cost, runtime_seconds=20 + index
            )
            await _insert_agent_score(conn, agent_id=rival, miner_hotkey=f"5FRival{index}", set_id=1, final_score=score)
        mine = await _insert_agent_in_set(
            conn, hotkey=HOTKEY_A, coldkey=COLDKEY, name="agent-a", set_id=1, created_at=BASE_TIME
        )
        await _run_validator_evaluations(conn, agent_id=mine, set_id=1, solved=1, cost_usd=0.20, runtime_seconds=40)
        await _insert_agent_score(conn, agent_id=mine, miner_hotkey=HOTKEY_A, set_id=1, final_score=0.5)

    clear_all_ttl_caches()
    leaderboard = {a.agent_id: a for a in await evaluation_sets_endpoint._build_leaderboard(1, 2)}
    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    agent = result[HOTKEY_A][0]
    assert agent.agent_id in leaderboard
    assert _metrics(agent) == _metrics(leaderboard[agent.agent_id])
    assert agent.rank == 3  # behind both foreign agents
    assert agent.final_score == pytest.approx(0.5)
    assert agent.validator_count == 1  # _insert_agent_score seeds agent_scores with 1
    assert agent.average_cost_usd == pytest.approx(0.20)
    assert agent.average_runtime_seconds == pytest.approx(40.0)
    assert sorted(agent.validator_hotkeys) == [VALIDATOR_A, VALIDATOR_B]


async def test_tentative_scores_included_for_evaluating_agent():
    """An agent still evaluating has no agent_scores row but is scored and ranked anyway."""
    async with _db.pool.acquire() as conn:
        await _setup_competition(conn, 1, SET_1_START)
        agent_id = await _insert_agent_in_set(
            conn,
            hotkey=HOTKEY_A,
            coldkey=COLDKEY,
            name="agent-a",
            set_id=1,
            created_at=BASE_TIME,
            status="evaluating",
        )
        await _run_validator_evaluations(conn, agent_id=agent_id, set_id=1, solved=2, cost_usd=0.10, runtime_seconds=30)

    clear_all_ttl_caches()
    leaderboard = {a.agent_id: a for a in await evaluation_sets_endpoint._build_leaderboard(1, 2)}
    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    agent = result[HOTKEY_A][0]
    assert agent.final_score == pytest.approx(1.0)
    assert sorted(agent.validator_hotkeys) == [VALIDATOR_A, VALIDATOR_B]
    assert agent.rank == 1
    assert _metrics(agent) == _metrics(leaderboard[agent.agent_id])


async def test_agents_across_two_sets_ranked_within_own_set():
    """One query, two competitions: each agent is ranked inside its own set."""
    async with _db.pool.acquire() as conn:
        await _setup_competition(conn, 1, SET_1_START, end=SET_2_START)
        await _setup_competition(conn, 2, SET_2_START)
        old = await _insert_agent_in_set(
            conn, hotkey=HOTKEY_A, coldkey=COLDKEY, name="agent-a", set_id=1, created_at=SET_1_START
        )
        new = await _insert_agent_in_set(
            conn, hotkey=HOTKEY_B, coldkey=COLDKEY, name="agent-b", set_id=2, created_at=SET_2_START
        )
        await _run_validator_evaluations(conn, agent_id=old, set_id=1, solved=2, cost_usd=0.10, runtime_seconds=30)
        await _run_validator_evaluations(conn, agent_id=new, set_id=2, solved=2, cost_usd=0.20, runtime_seconds=40)
        await _insert_agent_score(conn, agent_id=old, miner_hotkey=HOTKEY_A, set_id=1, final_score=1.0)
        await _insert_agent_score(conn, agent_id=new, miner_hotkey=HOTKEY_B, set_id=2, final_score=1.0)

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert result[HOTKEY_A][0].set_id == 1
    assert result[HOTKEY_B][0].set_id == 2
    assert result[HOTKEY_A][0].rank == 1  # rank 1 of its own set
    assert result[HOTKEY_B][0].rank == 1  # rank 1 of its own set
    assert result[HOTKEY_A][0].average_cost_usd == pytest.approx(0.10)
    assert result[HOTKEY_B][0].average_cost_usd == pytest.approx(0.20)


async def test_agents_the_ranking_does_not_cover_are_still_returned():
    """Agents outside the ranked population come back unranked rather than disappearing.

    Two ways an agent falls outside it: it has never been evaluated, or its set has no
    public leaderboard to rank against. Either way it keeps whatever agent_scores holds
    for it and simply has no rank.
    """
    async with _db.pool.acquire() as conn:
        await _setup_competition(conn, 1, SET_1_START)
        scored = await _insert_agent_in_set(
            conn, hotkey=HOTKEY_A, coldkey=COLDKEY, name="agent-a", set_id=1, created_at=BASE_TIME
        )
        await _run_validator_evaluations(conn, agent_id=scored, set_id=1, solved=2, cost_usd=0.10, runtime_seconds=30)
        await _insert_agent_score(conn, agent_id=scored, miner_hotkey=HOTKEY_A, set_id=1, final_score=1.0)
        await _insert_agent_in_set(
            conn,
            hotkey=HOTKEY_B,
            coldkey=COLDKEY,
            name="agent-b",
            set_id=1,
            created_at=BASE_TIME,
            status="screening_1",
        )

    result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    # never evaluated: nothing to report either way
    assert result[HOTKEY_B][0].rank is None
    assert result[HOTKEY_B][0].final_score is None
    assert result[HOTKEY_B][0].average_cost_usd is None

    # a set with no public leaderboard cannot rank, but the stored score survives
    clear_all_ttl_caches()
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET start_date = NULL WHERE set_id = 1")

    unranked = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)

    assert set(unranked) == {HOTKEY_A, HOTKEY_B}
    assert unranked[HOTKEY_A][0].rank is None
    assert unranked[HOTKEY_A][0].final_score == pytest.approx(1.0)
    assert unranked[HOTKEY_A][0].set_id == 1


async def test_disqualified_agent_has_null_rank():
    """A banned coldkey's agent is disqualified: no rank, and it consumes no rank slot."""
    async with _db.pool.acquire() as conn:
        await _setup_competition(conn, 1, SET_1_START)
        banned = await _insert_agent_in_set(
            conn, hotkey="5FBanned", coldkey=OTHER_COLDKEY, name="banned", set_id=1, created_at=BASE_TIME
        )
        mine = await _insert_agent_in_set(
            conn, hotkey=HOTKEY_A, coldkey=COLDKEY, name="agent-a", set_id=1, created_at=BASE_TIME
        )
        await _run_validator_evaluations(conn, agent_id=banned, set_id=1, solved=2, cost_usd=0.01, runtime_seconds=10)
        await _run_validator_evaluations(conn, agent_id=mine, set_id=1, solved=1, cost_usd=0.20, runtime_seconds=40)
        await _insert_agent_score(conn, agent_id=banned, miner_hotkey="5FBanned", set_id=1, final_score=1.0)
        await _insert_agent_score(conn, agent_id=mine, miner_hotkey=HOTKEY_A, set_id=1, final_score=0.5)
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test')", OTHER_COLDKEY
        )

    banned_result = await retrieval_module.agents_by_coldkey(miner_coldkey=OTHER_COLDKEY)
    assert banned_result["5FBanned"][0].disqualified is True
    assert banned_result["5FBanned"][0].rank is None

    clear_all_ttl_caches()
    mine_result = await retrieval_module.agents_by_coldkey(miner_coldkey=COLDKEY)
    # the disqualified agent sits in its own partition, so ours is still rank 1
    assert mine_result[HOTKEY_A][0].rank == 1
    assert mine_result[HOTKEY_A][0].disqualified is False
