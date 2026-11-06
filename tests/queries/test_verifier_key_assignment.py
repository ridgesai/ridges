from __future__ import annotations

import json

import pytest

import queries.evaluation as evaluation_queries
import utils.database as db
from api.src.utils.openrouter_keys import MintedKey, OpenRouterKeyError
from models.agent import AgentStatus
from queries.agent import EvaluationCandidate
from queries.evaluation import create_new_evaluation_and_evaluation_runs
from tests.queries.test_competition_lifecycle_gates import _insert_agent, _seed_competition

pytestmark = pytest.mark.anyio
HOTKEY = "validator-a"
SET_ID = 27
VERIFIER_SPEC = {"required_verifier_env": ["OPENROUTER_VERIFIER_API_KEY"]}


@pytest.fixture(autouse=True)
async def clean(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE competitions, agents, evaluation_sets, evaluations, evaluation_runs, "
            "competition_admin_events, validator_competition_allowlists RESTART IDENTITY CASCADE"
        )
        await conn.execute("UPDATE competition_work_cursors SET last_served_set_id = NULL")


@pytest.fixture
def mint_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_mint(**kwargs):
        calls.append(kwargs)
        return MintedKey(secret="sk-or-v1-minted", key_hash="h1")

    monkeypatch.setattr(evaluation_queries, "mint_verifier_key", fake_mint)
    return calls


async def _seed(*, needs_verifier: bool) -> EvaluationCandidate:
    async with db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=SET_ID)
        agent_id = await _insert_agent(conn, set_id=SET_ID, status=AgentStatus.evaluating)
        if needs_verifier:
            await conn.execute(
                "UPDATE evaluation_sets SET execution_spec = $1::jsonb WHERE set_id = $2",
                json.dumps(VERIFIER_SPEC),
                SET_ID,
            )
    return EvaluationCandidate(agent_id=agent_id, set_id=SET_ID)


async def _assert_nothing_persisted() -> None:
    async with db.pool.acquire() as conn:
        for table in ("evaluations", "evaluation_runs", "evaluation_run_attempts"):
            assert await conn.fetchval(f"SELECT COUNT(*) FROM {table}") == 0, table
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'validator'")
            is None
        )


async def test_verifier_set_mints_one_key_for_its_runs(mint_calls):
    candidate = await _seed(needs_verifier=True)

    assignment = await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None)

    assert assignment is not None
    assert assignment.verifier_key == MintedKey(secret="sk-or-v1-minted", key_hash="h1")
    assert mint_calls == [{"evaluation_id": assignment.evaluation.evaluation_id, "run_count": 1}]
    assert "sk-or" not in repr(assignment)


async def test_mint_counts_only_runs_that_need_the_key(mint_calls):
    candidate = await _seed(needs_verifier=False)
    async with db.pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO evaluation_sets (set_id, set_group, problem_name, execution_spec)
            VALUES ($1, 'validator'::evaluationsetgroup, $2, $3::jsonb)
            """,
            [(SET_ID, f"{SET_ID}-verifier-{i}", json.dumps(VERIFIER_SPEC)) for i in range(2)],
        )

    assignment = await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None)

    assert assignment is not None
    assert len(assignment.evaluation_runs) == 3
    assert mint_calls == [{"evaluation_id": assignment.evaluation.evaluation_id, "run_count": 2}]


async def test_keyless_set_never_mints(mint_calls):
    candidate = await _seed(needs_verifier=False)

    assignment = await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None)

    assert assignment is not None
    assert assignment.verifier_key is None
    assert mint_calls == []


async def test_mint_failure_rolls_back_evaluation(monkeypatch):
    candidate = await _seed(needs_verifier=True)

    async def failing_mint(**_kwargs):
        raise OpenRouterKeyError("Minting verifier key failed")

    monkeypatch.setattr(evaluation_queries, "mint_verifier_key", failing_mint)

    with pytest.raises(OpenRouterKeyError):
        await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None)

    await _assert_nothing_persisted()


async def test_disallowed_mint_rolls_back_without_calling_openrouter(mint_calls):
    candidate = await _seed(needs_verifier=True)

    with pytest.raises(OpenRouterKeyError):
        await create_new_evaluation_and_evaluation_runs(candidate, HOTKEY, None, allow_verifier_key_mint=False)

    assert mint_calls == []
    await _assert_nothing_persisted()
