import pytest

import utils.database as _db

SET_ID = 71
VALIDATORS = ("val-1", "val-2")


@pytest.fixture(autouse=True)
async def clean(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE disqualified_problems, agent_scores, evaluations, evaluation_runs, "
            "evaluation_sets, agents RESTART IDENTITY CASCADE"
        )
    yield


async def _seed_problem(conn, name):
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, benchmark_family, created_at)
        VALUES ($1, 'validator', $2, 'swebench', NOW())
        """,
        SET_ID,
        name,
    )


async def _seed_agent_with_runs(conn, hotkey, solved_by_problem):
    """solved_by_problem: dict problem_name -> bool. Creates one evaluation per validator,
    each with a run per problem, so consensus (all validators solved) can be computed."""
    agent_id = await conn.fetchval(
        """
        INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
        VALUES (gen_random_uuid(), $1, $1, 1, 'finished', NOW(), '127.0.0.1')
        RETURNING agent_id
        """,
        hotkey,
    )
    for validator in VALIDATORS:
        eval_id = await conn.fetchval(
            """
            INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at,
                                     evaluation_set_group, finished_at)
            VALUES (gen_random_uuid(), $1, $2, $3, NOW(), 'validator', NOW())
            RETURNING evaluation_id
            """,
            agent_id,
            validator,
            SET_ID,
        )
        for problem_name, solved in solved_by_problem.items():
            # `solved` in evaluation_runs_hydrated is computed: true iff test_results is a non-empty
            # JSONB array whose elements ALL have status='pass'. So seed test_results accordingly.
            test_results = '[{"status": "pass"}]' if solved else '[{"status": "fail"}]'
            await conn.execute(
                """
                INSERT INTO evaluation_runs (evaluation_run_id, evaluation_id, problem_name, status,
                                             test_results, created_at, started_running_agent_at,
                                             finished_or_errored_at)
                VALUES (gen_random_uuid(), $1, $2, 'finished', $3::jsonb, NOW(), NOW(), NOW())
                """,
                eval_id,
                problem_name,
                test_results,
            )
    return agent_id


async def _final_score(conn, agent_id):
    return await conn.fetchval("SELECT final_score FROM agent_scores WHERE agent_id = $1", agent_id)


@pytest.mark.anyio
async def test_disqualifying_a_problem_drops_it_from_the_denominator():
    async with _db.pool.acquire() as conn:
        await _seed_problem(conn, "p_good")
        await _seed_problem(conn, "p_flaky")
        # Agent solves p_good on all validators, fails p_flaky -> 1/2 before DQ, 1/1 after.
        agent_id = await _seed_agent_with_runs(conn, "hk-a", {"p_good": True, "p_flaky": False})
        await conn.execute("SELECT refresh_agent_scores_for_agent($1)", agent_id)
        assert await _final_score(conn, agent_id) == pytest.approx(0.5)

        await conn.execute(
            """
            INSERT INTO disqualified_problems (set_id, set_group, problem_name, reason)
            VALUES ($1, 'validator', 'p_flaky', 'flaky')
            """,
            SET_ID,
        )
        await conn.execute("SELECT refresh_agent_scores_for_agent($1)", agent_id)
        assert await _final_score(conn, agent_id) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_get_set_problems_excludes_disqualified():
    from models.evaluation_set import EvaluationSetGroup
    from queries.evaluation_set import get_all_evaluation_set_problems_in_set_group_in_set_id

    async with _db.pool.acquire() as conn:
        await _seed_problem(conn, "p_good")
        await _seed_problem(conn, "p_flaky")
        await conn.execute(
            """
            INSERT INTO disqualified_problems (set_id, set_group, problem_name, reason)
            VALUES ($1, 'validator', 'p_flaky', 'flaky')
            """,
            SET_ID,
        )

    problems = await get_all_evaluation_set_problems_in_set_group_in_set_id(SET_ID, EvaluationSetGroup.validator)
    names = {p.problem_name for p in problems}
    assert names == {"p_good"}
