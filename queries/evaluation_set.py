import json
from datetime import datetime
from typing import List

import asyncpg

from models.evaluation_set import (
    EvaluationSet,
    EvaluationSetGroup,
    EvaluationSetProblem,
    NewEvaluationSetProblem,
)
from queries._row_parsing import parse_jsonb_fields
from utils.database import DatabaseConnection, db_operation


def _parse_evaluation_set_problem_from_row(
    row: asyncpg.Record,
) -> EvaluationSetProblem:
    return EvaluationSetProblem(**parse_jsonb_fields(row, "execution_spec"))


@db_operation
async def get_latest_set_id(conn: DatabaseConnection) -> int | None:
    return await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")


@db_operation
async def get_set_created_at(conn: DatabaseConnection, set_id: int) -> datetime:
    return await conn.fetchval("SELECT MIN(created_at) FROM evaluation_sets WHERE set_id = $1", set_id)


@db_operation
async def get_all_problem_names_in_set_group_in_set_id(
    conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup
) -> list[tuple[str, str | None]]:
    results = await conn.fetch(
        """
        SELECT problem_name, problem_suite_name
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value,
    )

    return [(row["problem_name"], row["problem_suite_name"]) for row in results]


@db_operation
async def get_all_evaluation_set_problems_in_set_group_in_set_id(
    conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup
) -> List[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value,
    )

    return [_parse_evaluation_set_problem_from_row(result) for result in results]


@db_operation
async def get_all_evaluation_set_problems_for_set_id(
    conn: DatabaseConnection, set_id: int
) -> list[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluation_sets
        WHERE set_id = $1
        ORDER BY set_group, problem_name
        """,
        set_id,
    )

    return [_parse_evaluation_set_problem_from_row(result) for result in results]


@db_operation
async def create_evaluation_set_problems(
    conn: DatabaseConnection,
    set_id: int,
    problems: List[NewEvaluationSetProblem],
) -> None:
    await conn.executemany(
        """
        INSERT INTO evaluation_sets (
            set_id,
            set_group,
            problem_name,
            problem_suite_name,
            benchmark_family,
            execution_spec,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
        """,
        [
            (
                set_id,
                problem.set_group.value,
                problem.problem_name,
                problem.problem_suite_name or problem.benchmark_family,
                problem.benchmark_family,
                json.dumps(problem.execution_spec),
            )
            for problem in problems
        ],
    )


@db_operation
async def get_all_evaluation_sets(
    conn: DatabaseConnection,
) -> list[EvaluationSet]:
    results = await conn.fetch(
        """
        SELECT
            es.set_id,
            MIN(es.created_at) AS created_at,
            c.name AS competition_name,
            c.start_date AS competition_start_date,
            c.end_date AS competition_end_date
        FROM evaluation_sets es
        LEFT JOIN competitions c ON c.set_id = es.set_id
        GROUP BY es.set_id, c.name, c.start_date, c.end_date
        ORDER BY es.set_id
        """
    )
    return [EvaluationSet(**row) for row in results]


@db_operation
async def get_evaluation_set_submission_stats(conn: DatabaseConnection, set_id: int) -> asyncpg.Record:
    """Retrieve submission statistics for a specific evaluation set.

    Parameters
    ----------
    conn : DatabaseConnection
        The database connection to use for the query.
    set_id : int
        The evaluation set ID for which to retrieve submission statistics.
    Returns
    -------
    asyncpg.Record
        Returns a record with the following fields:
        - total_agents: The total number of agents that participated in the evaluation set.
        - unique_miners: The count of unique miner hotkeys associated with the agents in the set.
        - failed_at_pre_screening_count: The count of agents that failed at the pre-screening stage.
        - failed_at_screener_1_count: The count of agents that failed at the first screening stage.
        - failed_at_screener_2_count: The count of agents that failed at the second screening stage.
        - finished_at_validator_count: The count of agents that finished the validation stage.
        - approved_emission_count: The count of agents that were approved for emission.
    """
    return await conn.fetchrow(
        """
        with set_window as (
            select
                COALESCE(
                    (SELECT start_date FROM competitions WHERE set_id = $1),
                    (SELECT MIN(created_at) FROM evaluation_sets WHERE set_id = $1)
                ) as set_start,
                -- TODO: fallback end-boundary via next set's created_at may not correctly
                -- capture all competing agents; set competition_end_date explicitly to fix.
                COALESCE(
                    (SELECT end_date FROM competitions WHERE set_id = $1),
                    (SELECT MIN(created_at) FROM evaluation_sets
                     WHERE set_id = (SELECT MIN(set_id) FROM evaluation_sets WHERE set_id > $1))
                ) as set_end
        ),
        agents_in_window as materialized (
            select
                a.agent_id,
                a.miner_hotkey,
                a.status
            from
                agents a
                cross join set_window sw
            where
                a.created_at >= sw.set_start
                and (
                    sw.set_end is null
                    or a.created_at < sw.set_end
                )
                and a.agent_id not in (
                    select
                        agent_id
                    from
                        benchmark_agent_ids
                )
        ),
        last_evaluation_per_agent as (
            select
                distinct on (agent_id) *
            from
                evaluations
            where
                set_id = $1
            order by
                agent_id,
                created_at desc
        )
        select
            COUNT(distinct aiw.agent_id) :: int as total_agents,
            COUNT(distinct aiw.miner_hotkey) :: int as unique_miners,
            COUNT(*) filter (
                where
                    aiw.status in (
                        'failed_pre_screening',
                        'pre_screening_needs_review'
                    )
            ) :: int as failed_at_pre_screening_count,
            COUNT(
                distinct case
                    when e.agent_id is not null
                    and (
                        (
                            e.evaluation_set_group = 'screener_1'
                            and aiw.status = 'cancelled'
                        )
                        or aiw.status = 'failed_screening_1'
                    ) then aiw.agent_id
                end
            ) :: int as failed_at_screener_1_count,
            COUNT(
                distinct case
                    when e.agent_id is not null
                    and (
                        (
                            e.evaluation_set_group = 'screener_2'
                            and aiw.status = 'cancelled'
                        )
                        or aiw.status = 'failed_screening_2'
                    ) then aiw.agent_id
                end
            ) :: int as failed_at_screener_2_count,
            COUNT(
                distinct case
                    when e.agent_id is not null
                    and aiw.status = 'finished' then aiw.agent_id
                end
            ) :: int as finished_at_validator_count,
            COUNT(distinct aa.agent_id) :: int as approved_emission_count
        from
            agents_in_window aiw
            left join last_evaluation_per_agent e on e.agent_id = aiw.agent_id
            left join approved_agents aa on aa.agent_id = aiw.agent_id
            and aa.set_id = $1
        """,
        set_id,
    )


@db_operation
async def get_evaluation_set_score_stats(conn: DatabaseConnection, set_id: int) -> asyncpg.Record:
    """_summary_

    Parameters
    ----------
    conn : DatabaseConnection
        Database connection to use for the query.
    set_id : int
        The evaluation set ID for which to retrieve score statistics.
    Returns
    -------
    asyncpg.Record
        Returns a record with the following fields:
        - best: The best final score achieved by any agent in the set.
        - average: The average final score of all agents in the set.
        - above_50: The count of agents with a final score above 0.5.
        - above_75: The count of agents with a final score above 0.75.
        - above_90: The count of agents with a final score above 0.9.
        - prev_best_score: The best final score from the previous evaluation set.
        - agents_beating_previous_best: The count of agents in the current set that beat the previous best score.
    """
    return await conn.fetchrow(
        """
        WITH prev_best AS (
            SELECT
                MAX(final_score) AS score
            FROM
                agent_scores
            WHERE
                set_id = (
                    SELECT
                        MAX(set_id)
                    FROM
                        evaluation_sets
                    WHERE
                        set_id < $1
                )
                AND agent_id NOT IN (
                    SELECT
                        agent_id
                    FROM
                        benchmark_agent_ids
                )
        )
        SELECT
            MAX(s.final_score) AS best,
            AVG(s.final_score) AS average,
            COUNT(*) FILTER (
                WHERE
                    s.final_score > 0.5
            ) :: int AS above_50,
            COUNT(*) FILTER (
                WHERE
                    s.final_score > 0.75
            ) :: int AS above_75,
            COUNT(*) FILTER (
                WHERE
                    s.final_score > 0.9
            ) :: int AS above_90,
            (
                SELECT
                    score
                FROM
                    prev_best
            ) AS prev_best_score,
            COUNT(*) FILTER (
                WHERE
                    s.final_score > (
                        SELECT
                            score
                        FROM
                            prev_best
                    )
            ) :: int AS agents_beating_previous_best
        FROM
            agent_scores s
        WHERE
            s.set_id = $1
            AND s.agent_id NOT IN (
                SELECT
                    agent_id
                FROM
                    benchmark_agent_ids
            )
            AND s.status = 'finished'
        """,
        set_id,
    )
