from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.approval import enqueue_approval_job, finish_agent_and_enqueue_approval
from queries.errors import AgentCompetitionMembershipMismatchError

_CLEAN = (
    "TRUNCATE approval_jobs, agent_approval_states, pre_screening_results, pre_screening_jobs, agents, competitions "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(_CLEAN)
        await _insert_competition(conn)
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(_CLEAN)


async def _insert_competition(conn, *, policy_version: str = "competition-policy-v1") -> None:
    await conn.execute(
        """
        INSERT INTO competitions (
            set_id, start_date, scoring_mode, screener_1_threshold, screener_2_threshold,
            prune_threshold, required_validator_count, pre_screening_enabled,
            auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
            incentive_performance_threshold, incentive_cost_threshold,
            incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
        ) VALUES (7, NOW(), 'consensus', 0.4, 0.4, 0.4, 3, true, true, $1, false, 0.03, 0.06, 336, 12)
        """,
        policy_version,
    )


async def _insert_agent(status: str = "finished") -> UUID:
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id)
            VALUES ($1, $2, 'test-agent', 0, $3::agentstatus, NOW(), '127.0.0.1', 7)
            """,
            agent_id,
            f"hotkey-{agent_id}",
            status,
        )
    return agent_id


async def _insert_pre_screening(
    agent_id: UUID,
    *,
    policy_version: str,
    status: str,
    reviewer_id: str | None,
    with_result: bool,
    result_policy_version: str | None = None,
) -> None:
    async with _db.pool.acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO pre_screening_jobs (agent_id, set_id, policy_version, status, reviewer_id, reviewed_at)
            VALUES ($1, 7, $2, $3, $4, CASE WHEN $4::text IS NULL THEN NULL ELSE NOW() END)
            RETURNING job_id
            """,
            agent_id,
            policy_version,
            status,
            reviewer_id,
        )
        if with_result:
            await conn.execute(
                """
                INSERT INTO pre_screening_results (
                    job_id, agent_id, verdict, confidence, summary, categories, evidence, static_findings,
                    model, fallback_used, policy_version, raw_response
                ) VALUES ($1, $2, 'pass', 0.9, 'ok', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'm', false, $3, '{}'::jsonb)
                """,
                job_id,
                agent_id,
                result_policy_version or policy_version,
            )


async def _approval_row(agent_id: UUID):
    async with _db.pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT policy_version, input_snapshot FROM approval_jobs WHERE agent_id = $1", agent_id
        )


def _snapshot(row) -> dict:
    snapshot = row["input_snapshot"]
    return json.loads(snapshot) if isinstance(snapshot, str) else snapshot


@pytest.mark.anyio
async def test_auto_pre_screening_policy_is_copied_into_approval() -> None:
    agent_id = await _insert_agent()
    await _insert_pre_screening(
        agent_id, policy_version="hardcoding-linting-v1", status="succeeded", reviewer_id=None, with_result=True
    )

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is not None

    row = await _approval_row(agent_id)
    assert row["policy_version"] == "competition-policy-v1"
    context = _snapshot(row)["evaluation_context"]["pre_screening"]
    assert context["resolution"] == "auto"
    assert context["policy_version"] == "hardcoding-linting-v1"
    assert context["verdict"] == "pass"


@pytest.mark.anyio
async def test_human_pre_screening_policy_is_copied_into_approval() -> None:
    agent_id = await _insert_agent()
    await _insert_pre_screening(
        agent_id,
        policy_version="hardcoding-linting-v1",
        status="succeeded",
        reviewer_id="reviewer-1",
        with_result=False,
    )

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is not None

    row = await _approval_row(agent_id)
    assert row["policy_version"] == "competition-policy-v1"
    context = _snapshot(row)["evaluation_context"]["pre_screening"]
    assert context["resolution"] == "human"
    assert context["policy_version"] == "hardcoding-linting-v1"


@pytest.mark.anyio
async def test_no_pre_screening_job_uses_competition_policy() -> None:
    agent_id = await _insert_agent()

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is not None

    row = await _approval_row(agent_id)
    assert row["policy_version"] == "competition-policy-v1"
    assert _snapshot(row)["evaluation_context"]["pre_screening"] is None


@pytest.mark.anyio
async def test_finish_agent_and_enqueue_approval_copies_latest_pre_screening_policy() -> None:
    agent_id = await _insert_agent(status="evaluating")
    await _insert_pre_screening(
        agent_id, policy_version="hardcoding-v1", status="failed", reviewer_id="reviewer-1", with_result=False
    )
    await _insert_pre_screening(
        agent_id, policy_version="hardcoding-linting-v1", status="succeeded", reviewer_id=None, with_result=True
    )

    assert await finish_agent_and_enqueue_approval(agent_id=agent_id, set_id=7) is True

    row = await _approval_row(agent_id)
    assert row["policy_version"] == "competition-policy-v1"
    assert _snapshot(row)["evaluation_context"]["pre_screening"]["policy_version"] == "hardcoding-linting-v1"


@pytest.mark.anyio
async def test_job_policy_is_canonical_over_result_row_policy() -> None:
    """The job row is the write-once source; a (corrupt or manually edited) result row must not win."""
    agent_id = await _insert_agent()
    await _insert_pre_screening(
        agent_id,
        policy_version="hardcoding-linting-v1",
        status="succeeded",
        reviewer_id=None,
        with_result=True,
        result_policy_version="hardcoding-v1",
    )

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is not None

    row = await _approval_row(agent_id)
    assert row["policy_version"] == "competition-policy-v1"
    assert _snapshot(row)["evaluation_context"]["pre_screening"]["policy_version"] == "hardcoding-linting-v1"


@pytest.mark.anyio
async def test_active_job_retry_keeps_original_competition_policy() -> None:
    agent_id = await _insert_agent()

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is not None
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE competitions SET hardcoding_policy_version = 'competition-policy-v2' WHERE set_id = 7"
        )

    assert await enqueue_approval_job(agent_id=agent_id, set_id=7) is None
    assert (await _approval_row(agent_id))["policy_version"] == "competition-policy-v1"


@pytest.mark.anyio
async def test_approval_rejects_set_other_than_locked_agent_membership() -> None:
    agent_id = await _insert_agent()

    with pytest.raises(AgentCompetitionMembershipMismatchError):
        await enqueue_approval_job(agent_id=agent_id, set_id=8)
