from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.agent import get_public_agent_by_id


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE agents, competitions RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE agents, competitions RESTART IDENTITY CASCADE")


async def _insert_agent(conn, *, miner_hotkey: str, set_id: int | None = None) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, name, version_num, status,
            created_at, ip_address, set_id
        )
        VALUES ($1, $2, $2, 1, 'finished', NOW(), '127.0.0.1', $3)
        """,
        agent_id,
        miner_hotkey,
        set_id,
    )
    return agent_id


async def _insert_evaluation(conn, *, agent_id: UUID, set_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id, agent_id, validator_hotkey, set_id,
            evaluation_set_group, created_at
        )
        VALUES ($1, $2, 'validator-hotkey', $3, 'validator', NOW())
        """,
        uuid4(),
        agent_id,
        set_id,
    )


async def _insert_score(conn, *, agent_id: UUID, miner_hotkey: str, set_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, validator_count, final_score
        )
        VALUES ($1, $2, $2, 1, NOW(), 'finished', $3, false, 1, 0.5)
        """,
        agent_id,
        miner_hotkey,
        set_id,
    )


async def _insert_review(conn, *, agent_id: UUID, set_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO agent_approval_states (
            agent_id, set_id, processing_status, updated_at
        )
        VALUES ($1, $2, 'needs_review', NOW())
        """,
        agent_id,
        set_id,
    )


async def _insert_approval(conn, *, agent_id: UUID, set_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO approved_agents (
            agent_id, set_id, approved_at, relative_improvement_units
        )
        VALUES ($1, $2, NOW(), 1.5)
        """,
        agent_id,
        set_id,
    )


@pytest.mark.anyio
async def test_modern_agent_uses_its_recorded_set_as_public_context() -> None:
    async with _db.pool.acquire() as conn:
        await conn.execute("INSERT INTO competitions (set_id) VALUES (100)")
        agent_id = await _insert_agent(conn, miner_hotkey="modern-hotkey", set_id=100)

        # Deliberately inconsistent related rows must not move a modern agent
        # away from the competition captured when it was uploaded.
        await _insert_evaluation(conn, agent_id=agent_id, set_id=101)
        await _insert_approval(conn, agent_id=agent_id, set_id=101)

    agent = await get_public_agent_by_id(agent_id)

    assert agent is not None
    assert agent.status == "finished"
    assert agent.competition_state is not None
    assert agent.competition_state.set_id == 100
    assert agent.competition_state.status == "didnt_qualify"
    assert agent.competition_state.approved is False
    assert agent.competition_state.relative_improvement_units is None


@pytest.mark.anyio
async def test_legacy_agent_context_follows_public_detail_compatibility_order() -> None:
    async with _db.pool.acquire() as conn:
        approval_agent = await _insert_agent(conn, miner_hotkey="approval-hotkey")
        review_agent = await _insert_agent(conn, miner_hotkey="review-hotkey")
        score_agent = await _insert_agent(conn, miner_hotkey="score-hotkey")
        evaluation_agent = await _insert_agent(conn, miner_hotkey="evaluation-hotkey")

        for agent_id in (approval_agent, review_agent, score_agent, evaluation_agent):
            await _insert_evaluation(conn, agent_id=agent_id, set_id=103)

        await _insert_approval(conn, agent_id=approval_agent, set_id=100)
        await _insert_review(conn, agent_id=approval_agent, set_id=101)
        await _insert_score(conn, agent_id=approval_agent, miner_hotkey="approval-hotkey", set_id=102)

        await _insert_review(conn, agent_id=review_agent, set_id=101)
        await _insert_score(conn, agent_id=review_agent, miner_hotkey="review-hotkey", set_id=102)

        await _insert_score(conn, agent_id=score_agent, miner_hotkey="score-hotkey", set_id=102)

    approval = await get_public_agent_by_id(approval_agent)
    review = await get_public_agent_by_id(review_agent)
    score = await get_public_agent_by_id(score_agent)
    evaluation = await get_public_agent_by_id(evaluation_agent)

    assert approval is not None and approval.competition_state is not None
    assert approval.competition_state.set_id == 100
    assert approval.competition_state.status == "baseline"
    assert approval.competition_state.approved is True

    assert review is not None and review.competition_state is not None
    assert review.competition_state.set_id == 101
    assert review.competition_state.status == "under_review"

    assert score is not None and score.competition_state is not None
    assert score.competition_state.set_id == 102
    assert score.competition_state.status == "didnt_qualify"
    assert score.competition_state.final_score == 0.5

    assert evaluation is not None and evaluation.competition_state is not None
    assert evaluation.competition_state.set_id == 103
    assert evaluation.competition_state.status == "didnt_qualify"
