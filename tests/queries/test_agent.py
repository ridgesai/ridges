"""Integration tests for get_next_agent_id_awaiting_evaluation_for_validator_hotkey."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import utils.database as _db
from queries.agent import (
    get_next_agent_id_awaiting_evaluation_for_validator_hotkey,
)

HOTKEY = "validator-hotkey-1"
OTHER_HOTKEY = "validator-hotkey-2"

# anyio_backend is defined in tests/conftest.py and inherited automatically.


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_runs, evaluations, benchmark_agent_ids, agents RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_runs, evaluations, benchmark_agent_ids, agents RESTART IDENTITY CASCADE"
        )


async def _insert_agent(*, status: str = "evaluating", created_at: datetime | None = None) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
            VALUES ($1, $2, $3, $4, $5::agentstatus, $6, $7)
            """,
            agent_id,
            "test-hotkey",
            "test-agent",
            1,
            status,
            created_at or datetime.now(timezone.utc),
            "127.0.0.1",
        )
    return agent_id


async def _insert_evaluation(
    agent_id: uuid.UUID,
    *,
    group: str,
    validator_hotkey: str = HOTKEY,
    set_id: int = 1,
) -> uuid.UUID:
    evaluation_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group)
            VALUES ($1, $2, $3, $4, $5::evaluationsetgroup)
            """,
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            group,
        )
    return evaluation_id


async def _insert_run(
    evaluation_id: uuid.UUID,
    *,
    status: str,
    error_code: int | None = None,
    verifier_reward: float | None = None,
    test_results: list | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_runs (
                evaluation_run_id, evaluation_id, problem_name, status,
                error_code, verifier_reward, test_results, created_at
            )
            VALUES ($1, $2, $3, $4::evaluationrunstatus, $5, $6, $7::jsonb, $8)
            """,
            run_id,
            evaluation_id,
            "test-problem",
            status,
            error_code,
            verifier_reward,
            json.dumps(test_results) if test_results is not None else None,
            datetime.now(timezone.utc),
        )
    return run_id


@pytest.mark.anyio
async def test_returns_none_when_no_candidates():
    """No evaluating agents → None."""
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_returns_agent_with_no_evaluations():
    """Single evaluating agent with zero evaluations → returned."""
    agent_id = await _insert_agent()
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_id


@pytest.mark.anyio
async def test_skips_benchmark_agent():
    """Agent in benchmark_agent_ids is excluded even when evaluating."""
    agent_id = await _insert_agent()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, $2)",
            agent_id,
            "benchmark",
        )
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_already_evaluated_by_this_validator():
    """Agent with a finished validator-group eval by HOTKEY → already_evaluated=true → None."""
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=HOTKEY)
    await _insert_run(eval_id, status="finished")
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_at_running_eval_limit():
    """Agent already has NUM_EVALS_PER_AGENT=1 running validator eval → None.

    Uses OTHER_HOTKEY so the agent is not excluded by already_evaluated,
    isolating the count limit condition.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    # 'running_agent' is not 'finished' or 'error' → computed_status='running'
    await _insert_run(eval_id, status="running_agent")
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_skips_agent_at_finished_eval_limit():
    """Agent already has NUM_EVALS_PER_AGENT=1 finished validator eval → None.

    Uses OTHER_HOTKEY to isolate the count limit from already_evaluated.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(eval_id, status="finished")  # computed_status='success'
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result is None


@pytest.mark.anyio
async def test_failed_evals_dont_consume_slots():
    """A failed validator eval (error outside 1000-1999) is excluded by the lateral ON condition.

    The agent should still be returned because the failed eval does not count
    toward num_running_evals or num_finished_evals.
    """
    agent_id = await _insert_agent()
    eval_id = await _insert_evaluation(agent_id, group="validator", validator_hotkey=OTHER_HOTKEY)
    # error_code=9000 is outside 1000-1999 → computed_status='failure' → excluded by ON condition.
    await _insert_run(eval_id, status="error", error_code=9000)
    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_id


@pytest.mark.anyio
async def test_ordering_by_screener_2_score_uses_verifier_reward():
    """verifier_reward drives screener_2_score ordering, not test_results parsing.

    Agent A: verifier_reward=1.0 (solved=true)  → score=1.0, created AFTER agent B
    Agent B: verifier_reward=0.0 (solved=false) → score=0.0, test_results=[pass]

    Expect agent A returned first because score 1.0 > 0.0 despite being created later.
    """
    # Agent B created first — so created_at tiebreak alone would favour B
    now = datetime.now(timezone.utc)
    agent_b = await _insert_agent(created_at=now - timedelta(seconds=10))
    agent_a = await _insert_agent(created_at=now)

    eval_a = await _insert_evaluation(agent_a, group="screener_2", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(eval_a, status="finished", verifier_reward=1.0)

    eval_b = await _insert_evaluation(agent_b, group="screener_2", validator_hotkey=OTHER_HOTKEY)
    await _insert_run(
        eval_b,
        status="finished",
        verifier_reward=0.0,
        test_results=[{"status": "pass", "name": "t1"}],
    )

    result = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(HOTKEY)
    assert result == agent_a
