import pytest

import api.endpoints.evaluation_sets as evaluation_sets_endpoint
import utils.database as _db


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE evaluation_sets RESTART IDENTITY CASCADE")


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_all_sets():
    async with _db.pool.acquire() as conn:
        # Set 1 — multiple rows across different groups and problems
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            1,
            "screener_1",
            "problem-a",
        )
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            1,
            "screener_2",
            "problem-b",
        )
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            1,
            "validator",
            "problem-c",
        )
        # Set 2 — also multiple rows
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            2,
            "screener_1",
            "problem-a",
        )
        await conn.execute(
            "INSERT INTO evaluation_sets (set_id, set_group, problem_name) VALUES ($1, $2, $3)",
            2,
            "validator",
            "problem-b",
        )

    result = await evaluation_sets_endpoint.evaluation_sets_list()
    # GROUP BY set_id must collapse the 3+2 rows into exactly 2 sets
    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2


@pytest.mark.anyio
async def test_evaluation_sets_list_returns_empty_when_no_sets():
    result = await evaluation_sets_endpoint.evaluation_sets_list()
    assert result == []
