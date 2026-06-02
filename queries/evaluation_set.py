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

# ---------------------------------------------------------------------------
# Shared SQL fragments for evaluation-set window queries.
# These CTEs appear across get_evaluation_set_submission_stats,
# get_evaluation_set_leaderboard_agents, and get_evaluation_set_leaderboard_summary.
# $1 is always the set_id parameter.
# ---------------------------------------------------------------------------

_SQL_SET_WINDOW_CTE = """\
set_window AS (
    SELECT
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
)"""


def _sql_agents_in_window_cte(select_columns: str) -> str:
    return (
        f"agents_in_window AS MATERIALIZED (\n"
        f"    SELECT\n"
        f"        {select_columns}\n"
        f"    FROM agents a\n"
        f"    CROSS JOIN set_window sw\n"
        f"    WHERE a.created_at >= sw.set_start\n"
        f"      AND (sw.set_end IS NULL OR a.created_at < sw.set_end)\n"
        f"      AND NOT EXISTS (\n"
        f"          SELECT 1 FROM benchmark_agent_ids b WHERE b.agent_id = a.agent_id\n"
        f"      )\n"
        f")"
    )


def _sql_validator_metrics_cte(include_validator_hotkeys: bool) -> str:
    hotkeys_col = (
        ",\n        COALESCE(\n"
        "            ARRAY_AGG(DISTINCT eh.validator_hotkey ORDER BY eh.validator_hotkey)\n"
        "                FILTER (WHERE eh.validator_hotkey IS NOT NULL),\n"
        "            ARRAY[]::text[]\n"
        "        ) AS validator_hotkeys"
        if include_validator_hotkeys
        else ""
    )
    return (
        f"validator_metrics AS MATERIALIZED (\n"
        f"    SELECT\n"
        f"        eh.agent_id,\n"
        f"        AVG(eh.avg_cost_usd) AS average_cost_usd,\n"
        f"        AVG(eh.avg_running_secs) AS average_runtime_seconds"
        f"{hotkeys_col}\n"
        f"    FROM evaluations_hydrated eh\n"
        f"    JOIN agents_in_window aiw ON aiw.agent_id = eh.agent_id\n"
        f"    WHERE eh.set_id = $1\n"
        f"      AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup\n"
        f"      AND eh.status = 'success'::EvaluationStatus\n"
        f"    GROUP BY eh.agent_id\n"
        f")"
    )


def _sql_ranked_scores_cte(extra_select_columns: str = "", *, materialized: bool) -> str:
    mat = " MATERIALIZED" if materialized else ""
    return (
        f"ranked_scores AS{mat} (\n"
        f"    SELECT\n"
        f"        ass.agent_id"
        f"{extra_select_columns},\n"
        f"        ROW_NUMBER() OVER (\n"
        f"            ORDER BY\n"
        f"                ROUND(ass.final_score::numeric, 6) DESC,\n"
        f"                vm.average_cost_usd ASC NULLS LAST,\n"
        f"                aiw.created_at ASC,\n"
        f"                ass.agent_id ASC\n"
        f"        )::int AS rank\n"
        f"    FROM agent_scores ass\n"
        f"    JOIN agents_in_window aiw ON aiw.agent_id = ass.agent_id\n"
        f"    LEFT JOIN validator_metrics vm ON vm.agent_id = ass.agent_id\n"
        f"    WHERE ass.set_id = $1\n"
        f"      AND ass.status::text <> 'cancelled'\n"
        f")"
    )


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
        f"""
        WITH {_SQL_SET_WINDOW_CTE},
        {_sql_agents_in_window_cte("a.agent_id, a.miner_hotkey, a.status")},
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


@db_operation
async def get_evaluation_set_leaderboard_agents(conn: DatabaseConnection, set_id: int) -> list[asyncpg.Record]:
    """Return all competition-window agents with leaderboard fields for an evaluation set."""

    return await conn.fetch(
        f"""
        WITH {_SQL_SET_WINDOW_CTE},
        {_sql_agents_in_window_cte("a.agent_id, a.miner_hotkey, a.name, a.version_num, a.status::text, a.created_at")},
        {_sql_validator_metrics_cte(include_validator_hotkeys=True)},
        {_sql_ranked_scores_cte(materialized=False)}
        SELECT
            rs.rank,
            aiw.agent_id,
            aiw.miner_hotkey,
            aiw.name,
            aiw.version_num,
            aiw.status,
            (aa.agent_id IS NOT NULL) AS approved,
            vm.average_cost_usd,
            vm.average_runtime_seconds,
            COALESCE(vm.validator_hotkeys, ARRAY[]::text[]) AS validator_hotkeys,
            aiw.created_at
        FROM agents_in_window aiw
        LEFT JOIN ranked_scores rs ON rs.agent_id = aiw.agent_id
        LEFT JOIN validator_metrics vm ON vm.agent_id = aiw.agent_id
        LEFT JOIN approved_agents aa
            ON aa.agent_id = aiw.agent_id
           AND aa.set_id = $1
        ORDER BY
            rs.rank ASC NULLS LAST,
            aiw.created_at ASC,
            aiw.agent_id ASC
        """,
        set_id,
    )


@db_operation
async def get_evaluation_set_leaderboard_summary(conn: DatabaseConnection, set_id: int) -> asyncpg.Record:
    """Return top-agent and efficiency summary fields without returning all agents."""

    return await conn.fetchrow(
        f"""
        WITH {_SQL_SET_WINDOW_CTE},
        {_sql_agents_in_window_cte("a.agent_id, a.name, a.version_num, a.created_at")},
        {_sql_validator_metrics_cte(include_validator_hotkeys=False)},
        {_sql_ranked_scores_cte(",\n        aiw.name,\n        aiw.version_num,\n        ass.final_score", materialized=True)},
        top_agent AS (
            SELECT
                agent_id,
                name,
                version_num,
                final_score
            FROM ranked_scores
            WHERE rank = 1
        ),
        efficiency AS (
            SELECT
                MIN(vm.average_cost_usd) FILTER (WHERE rs.agent_id IS NOT NULL)
                    AS lowest_average_cost_usd_top_agents,
                MIN(vm.average_runtime_seconds) FILTER (WHERE rs.agent_id IS NOT NULL)
                    AS lowest_average_runtime_seconds_top_agents,
                AVG(vm.average_cost_usd) AS average_agent_cost_usd,
                AVG(vm.average_runtime_seconds) AS average_agent_runtime_seconds
            FROM validator_metrics vm
            LEFT JOIN ranked_scores rs ON rs.agent_id = vm.agent_id
        )
        SELECT
            ta.agent_id AS top_agent_id,
            ta.name AS top_agent_name,
            ta.version_num AS top_agent_version_num,
            ta.final_score AS top_agent_final_score,
            e.lowest_average_cost_usd_top_agents,
            e.lowest_average_runtime_seconds_top_agents,
            e.average_agent_cost_usd,
            e.average_agent_runtime_seconds
        FROM efficiency e
        LEFT JOIN top_agent ta ON TRUE
        """,
        set_id,
    )


@db_operation
async def get_approved_agents_for_set(conn: DatabaseConnection, set_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        -- agent_scores has one row per agent; set_id tracks the most recent set.
        -- Agents approved for this set but re-scored in a later set will not appear here.
        SELECT
            a.agent_id,
            a.miner_hotkey,
            a.name,
            a.version_num,
            a.created_at,
            ass.final_score
        FROM approved_agents aa
        JOIN agents a ON a.agent_id = aa.agent_id
        JOIN agent_scores ass ON ass.agent_id = aa.agent_id AND ass.set_id = $1
        WHERE aa.set_id = $1
          AND aa.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND ass.status = 'finished'
        ORDER BY ass.final_score DESC
        """,
        set_id,
    )
