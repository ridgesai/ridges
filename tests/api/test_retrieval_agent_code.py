from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

import api.endpoints.retrieval as retrieval_endpoint
import utils.database as _db
from queries.evaluation_set import get_latest_set_id

AGENT_CODE = "print('agent')"
SET_CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_sets, agents, agent_scores, benchmark_agent_ids, banned_coldkeys "
            "RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
def bypass_cache_and_s3(monkeypatch):
    monkeypatch.setattr(retrieval_endpoint, "_get_latest_set_id", get_latest_set_id)
    monkeypatch.setattr(
        retrieval_endpoint, "_cached_code_hiding_score_cutoff", retrieval_endpoint._code_hiding_score_cutoff
    )

    async def _fake_download(key: str) -> str:
        return AGENT_CODE

    monkeypatch.setattr(retrieval_endpoint, "download_text_file_from_s3", _fake_download)


async def _insert_eval_set(conn, set_id: int = 1) -> None:
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1, $2, $3, $4)",
        set_id,
        "validator",
        "problem-a",
        SET_CREATED,
    )


async def _insert_agent(conn, *, agent_id, status: str = "finished") -> None:
    await conn.execute(
        """INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
           VALUES ($1, $2, $3, $4, $5, NOW(), $6)""",
        agent_id,
        f"hotkey-{agent_id}",
        f"agent-{agent_id}",
        1,
        status,
        "127.0.0.1",
    )


async def _insert_agent_score(conn, *, agent_id, set_id: int = 1, final_score: float) -> None:
    await conn.execute(
        """INSERT INTO agent_scores
               (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, approved, validator_count, final_score)
           VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9)""",
        agent_id,
        f"hotkey-{agent_id}",
        f"agent-{agent_id}",
        1,
        "finished",
        set_id,
        False,
        1,
        final_score,
    )


async def _seed_scored_agents(conn, scores: list[float]) -> list[UUID]:
    """Insert one finished agent + score row per entry, returning agent ids in input order."""
    agent_ids = []
    for score in scores:
        agent_id = uuid4()
        await _insert_agent(conn, agent_id=agent_id)
        await _insert_agent_score(conn, agent_id=agent_id, final_score=score)
        agent_ids.append(agent_id)
    return agent_ids


def _spread_scores(count: int = 11) -> list[float]:
    """Distinct descending scores 0.90, 0.89, ... so cutoff = score of the 10th agent."""
    return [round(0.90 - i * 0.01, 2) for i in range(count)]


async def _expect_hidden(agent_id) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await retrieval_endpoint.agent_code(agent_id)
    assert exc_info.value.status_code == 403
    return exc_info.value


@pytest.mark.anyio
async def test_top_ranked_agent_code_is_hidden():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        agent_ids = await _seed_scored_agents(conn, _spread_scores())

    error = await _expect_hidden(agent_ids[0])
    assert "hidden" in error.detail.lower()


@pytest.mark.anyio
async def test_agent_below_cutoff_code_is_served():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        agent_ids = await _seed_scored_agents(conn, _spread_scores())

    # 11th agent (0.80) is below the 10th agent's score (0.81)
    assert await retrieval_endpoint.agent_code(agent_ids[10]) == AGENT_CODE


@pytest.mark.anyio
async def test_agents_tied_with_tenth_score_are_hidden():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        # Ranks 1-9 distinct, ranks 10-12 all tied at 0.81
        agent_ids = await _seed_scored_agents(conn, _spread_scores(9) + [0.81, 0.81, 0.81])

    await _expect_hidden(agent_ids[-1])


@pytest.mark.anyio
async def test_tie_cluster_extends_hiding_past_ten_agents_via_top_scores():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        # 12 copies tied at 0.90; 0.70 is still the 3rd-highest distinct score
        agent_ids = await _seed_scored_agents(conn, [0.90] * 12 + [0.80, 0.70])

    await _expect_hidden(agent_ids[-1])


@pytest.mark.anyio
async def test_rejected_agent_with_protected_score_is_hidden():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _seed_scored_agents(conn, _spread_scores())

        rejected_id = uuid4()
        await _insert_agent(conn, agent_id=rejected_id)
        await _insert_agent_score(conn, agent_id=rejected_id, final_score=0.95)
        await conn.execute(
            """INSERT INTO agent_approval_states (agent_id, set_id, processing_status, system_verdict)
               VALUES ($1, $2, 'completed', 'rejected')""",
            rejected_id,
            1,
        )

    await _expect_hidden(rejected_id)


@pytest.mark.anyio
async def test_rejected_agents_do_not_consume_protection_slots():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        qualified_ids = await _seed_scored_agents(conn, _spread_scores())

        rejected_id = uuid4()
        await _insert_agent(conn, agent_id=rejected_id)
        await _insert_agent_score(conn, agent_id=rejected_id, final_score=0.95)
        await conn.execute(
            """INSERT INTO agent_approval_states (agent_id, set_id, processing_status, system_verdict)
               VALUES ($1, $2, 'completed', 'rejected')""",
            rejected_id,
            1,
        )

    await _expect_hidden(qualified_ids[9])


@pytest.mark.anyio
async def test_benchmark_agent_code_is_served_even_with_top_score():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _seed_scored_agents(conn, _spread_scores())

        benchmark_id = uuid4()
        await _insert_agent(conn, agent_id=benchmark_id)
        await _insert_agent_score(conn, agent_id=benchmark_id, final_score=0.95)
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, 'benchmark')",
            benchmark_id,
        )

    assert await retrieval_endpoint.agent_code(benchmark_id) == AGENT_CODE


@pytest.mark.anyio
async def test_all_finished_agents_hidden_when_fewer_than_ten():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        agent_ids = await _seed_scored_agents(conn, [0.50, 0.40])

    await _expect_hidden(agent_ids[0])
    await _expect_hidden(agent_ids[1])


@pytest.mark.anyio
async def test_finished_agent_without_current_set_score_is_served():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _seed_scored_agents(conn, _spread_scores())

        unscored_id = uuid4()
        await _insert_agent(conn, agent_id=unscored_id)

    assert await retrieval_endpoint.agent_code(unscored_id) == AGENT_CODE


@pytest.mark.anyio
async def test_screening_agent_still_gets_screening_403():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        screening_id = uuid4()
        await _insert_agent(conn, agent_id=screening_id, status="screening_1")

    error = await _expect_hidden(screening_id)
    assert "still being screened" in error.detail


@pytest.mark.anyio
async def test_unknown_agent_still_gets_404():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)

    with pytest.raises(HTTPException) as exc_info:
        await retrieval_endpoint.agent_code(uuid4())
    assert exc_info.value.status_code == 404
