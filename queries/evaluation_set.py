import json
from datetime import datetime
from typing import List

import asyncpg

from api.config import EARLIEST_SET_ID_WITH_GOOD_DATA, NUM_EVALS_PER_AGENT
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
    return f"""
    agents_in_window AS MATERIALIZED (
        SELECT
            {select_columns},
            review.approval_review_status AS approval_review_status,
            CASE
                WHEN review.approval_review_status = 'rejected'
                    OR EXISTS (
                        SELECT 1
                        FROM banned_coldkeys bc
                        WHERE bc.miner_coldkey = a.miner_coldkey
                    )
                THEN true
                ELSE false
            END AS disqualified
        FROM agents a
        CROSS JOIN set_window sw
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = a.agent_id
            AND review.set_id = $1  
        WHERE (
                a.set_id = $1
                OR (
                    a.set_id is NULL
                    AND a.created_at >= sw.set_start
                    AND (sw.set_end IS NULL OR a.created_at < sw.set_end)
                )
        )
        AND NOT EXISTS (
            SELECT 1 FROM benchmark_agent_ids b WHERE b.agent_id = a.agent_id)
    )
    """


def _sql_validator_metrics_cte(include_validator_hotkeys: bool, agent_filter_cte: str = "agents_in_window") -> str:
    """This method returns a CTE that computes average validator cost and runtime per agent, over their 3 "valid" evaluations. A valid evaluation is one with the status set to 'success' or 'running' or that it was cancelled.


    Optionally includes an array of validator hotkeys for those 3 evaluations.

    Parameters
    ----------
    include_validator_hotkeys : bool
        Whether to include an array of validator hotkeys for the evaluations considered in the averages.
    agent_filter_cte : str
        Name of the CTE providing agent_id values to filter against. Defaults to "agents_in_window".

    Returns
    -------
    str
        The SQL string for the CTE.
    """
    hotkeys_col = (
        ",\n        ARRAY_AGG(sample.validator_hotkey) AS validator_hotkeys" if include_validator_hotkeys else ""
    )
    return (
        f"validator_metrics AS MATERIALIZED (\n"
        f"    SELECT\n"
        f"        sample.agent_id,\n"
        f"        AVG(sample.avg_cost_usd) AS average_cost_usd,\n"
        f"        AVG(sample.avg_running_secs) AS average_runtime_seconds"
        f"{hotkeys_col}\n"
        f"    FROM (\n"
        f"        SELECT\n"
        f"            ranked.agent_id,\n"
        f"            ranked.evaluation_id,\n"
        f"            ranked.validator_hotkey,\n"
        f"            ranked.avg_running_secs,\n"
        f"            ranked.avg_cost_usd,\n"
        f"            ranked.status,\n"
        f"            ROW_NUMBER() OVER (\n"
        f"                PARTITION BY ranked.agent_id\n"
        f"                ORDER BY ranked.status\n"
        f"            ) AS rn\n"
        f"        FROM (\n"
        f"            SELECT\n"
        f"                evaluations.agent_id,\n"
        f"                evaluations.evaluation_id,\n"
        f"                evaluations.validator_hotkey,\n"
        f"                AVG(\n"
        f"                    EXTRACT(\n"
        f"                        EPOCH\n"
        f"                        FROM\n"
        f"                            erh.finished_or_errored_at - erh.started_running_agent_at\n"
        f"                    )\n"
        f"                ) AS avg_running_secs,\n"
        f"                AVG(COALESCE(erh.cost_usd, 0)) AS avg_cost_usd,\n"
        f"                CASE\n"
        f"                    WHEN EVERY(\n"
        f"                        erh.status = 'finished'::evaluationrunstatus\n"
        f"                        OR erh.status = 'error'::evaluationrunstatus\n"
        f"                        AND erh.error_code >= 1000\n"
        f"                        AND erh.error_code <= 1999\n"
        f"                    ) THEN 'success'::text\n"
        f"                    WHEN EVERY(\n"
        f"                        erh.status = ANY (\n"
        f"                            ARRAY ['finished'::evaluationrunstatus, 'error'::evaluationrunstatus]\n"
        f"                        )\n"
        f"                    ) THEN 'failure'::text\n"
        f"                    ELSE 'running'::text\n"
        f"                END::evaluationstatus AS status,\n"
        f"                CASE\n"
        f"                    WHEN bool_or(erh.status = 'error'::evaluationrunstatus and erh.error_code = 3060) THEN true\n"
        f"                    ELSE false\n"
        f"                END AS cancelled\n"
        f"            FROM\n"
        f"                evaluations\n"
        f"                JOIN evaluation_runs_hydrated erh USING (evaluation_id)\n"
        f"            WHERE\n"
        f"                evaluations.evaluation_set_group = 'validator'::EvaluationSetGroup\n"
        f"                AND evaluations.set_id = $1\n"
        f"            GROUP BY\n"
        f"                evaluations.agent_id,\n"
        f"                evaluations.evaluation_id\n"
        f"        ) AS ranked\n"
        f"        JOIN {agent_filter_cte} aiw ON aiw.agent_id = ranked.agent_id\n"
        f"        where ranked.status in ('running', 'success') or ranked.cancelled is true\n"
        f"    ) AS sample\n"
        f"    WHERE sample.rn <= {NUM_EVALS_PER_AGENT}\n"
        f"    GROUP BY sample.agent_id\n"
        f")"
    )


def _sql_top_agent_for_summary() -> str:
    """Returns a CTE that selects the top agent for the leaderboard summary query."""
    return """
        top_agent as (
            SELECT
                sa.agent_id,
                aiw.name,
                aiw.version_num,
                sa.final_score,
                CASE
                    WHEN aiw.disqualified THEN NULL
                    ELSE ROW_NUMBER() OVER (
                        PARTITION BY aiw.disqualified
                        ORDER BY
                            ROUND(sa.final_score::numeric, 6) DESC,
                            vm.average_cost_usd ASC NULLS LAST,
                            aiw.created_at ASC,
                            sa.agent_id ASC
                    )::int
                END AS rank
            FROM agent_scores sa
            JOIN agents_in_window aiw ON aiw.agent_id = sa.agent_id
            LEFT JOIN validator_metrics vm ON vm.agent_id = sa.agent_id
            WHERE sa.set_id = $1
              AND aiw.status = 'finished'
              AND NOT aiw.disqualified
            ORDER BY rank
            LIMIT 1
        )
        """


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
        WHERE es.set_id >= $1
        GROUP BY es.set_id, c.name, c.start_date, c.end_date
        ORDER BY es.set_id
        """,
        EARLIEST_SET_ID_WITH_GOOD_DATA,
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
            COUNT(distinct aa.agent_id) FILTER (WHERE NOT aiw.disqualified) :: int as approved_emission_count
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
        f"""
        WITH {_SQL_SET_WINDOW_CTE},
        {_sql_agents_in_window_cte("a.agent_id, a.status")},
        prev_best AS (
            SELECT
                MAX(previous_score.final_score) AS score
            FROM
                agent_scores previous_score
            JOIN agents previous_agent ON previous_agent.agent_id = previous_score.agent_id
            WHERE
                previous_score.set_id = (
                    SELECT
                        MAX(set_id)
                    FROM
                        evaluation_sets
                    WHERE
                        set_id < $1
                )
                AND previous_score.agent_id NOT IN (
                    SELECT
                        agent_id
                    FROM
                        benchmark_agent_ids
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM banned_coldkeys bc
                    WHERE bc.miner_coldkey = previous_agent.miner_coldkey
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
            JOIN agents_in_window aiw ON aiw.agent_id = s.agent_id
        WHERE
            s.set_id = $1
            AND aiw.status = 'finished'
            AND NOT aiw.disqualified
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
        tentative_scores AS (
            WITH tentative_runs AS (
                SELECT eh.agent_id, eh.validator_hotkey, erh.problem_name,
                       (erh.solved IS TRUE OR erh.error_code = 3060) AS solved_effective,
                       aiw.disqualified, aiw.created_at, aiw.status AS agent_status
                FROM evaluations_hydrated eh
                JOIN agents_in_window aiw
                    ON aiw.agent_id = eh.agent_id AND aiw.status in ('evaluating','cancelled')
                JOIN evaluation_runs_hydrated erh ON erh.evaluation_id = eh.evaluation_id
                WHERE eh.set_id = $1
                  AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_scores ass
                      WHERE ass.agent_id = eh.agent_id AND ass.set_id = $1
                  )
            ),
            per_problem AS (
                SELECT
                    agent_id,
                    problem_name,
                    disqualified,
                    created_at,
                    agent_status,
                    COUNT(DISTINCT validator_hotkey) FILTER (WHERE solved_effective)
                        AS solved_validator_count
                FROM tentative_runs
                GROUP BY agent_id, problem_name, disqualified, created_at, agent_status
            ),
            problem_count AS (
                SELECT COUNT(*)::float AS n
                FROM evaluation_sets
                WHERE set_id = $1 AND set_group = 'validator'::EvaluationSetGroup
            ),
            validator_counts AS (
                SELECT
                    agent_id,
                    COUNT(DISTINCT validator_hotkey)::int AS validator_count,
                    ARRAY_AGG(DISTINCT validator_hotkey ORDER BY validator_hotkey) AS validator_hotkeys
                FROM tentative_runs
                GROUP BY agent_id
            )
            SELECT
                pp.agent_id,
                COUNT(*) FILTER (WHERE pp.solved_validator_count >= vc.validator_count)::float
                    / NULLIF((SELECT n FROM problem_count), 0) AS final_score,
                vc.validator_count,
                vc.validator_hotkeys,
                pp.disqualified,
                pp.created_at,
                pp.agent_status
            FROM per_problem pp
            JOIN validator_counts vc ON vc.agent_id = pp.agent_id
            GROUP BY pp.agent_id, vc.validator_count, vc.validator_hotkeys, pp.disqualified, pp.created_at, pp.agent_status
            HAVING COUNT(*) FILTER (WHERE pp.solved_validator_count >= vc.validator_count) > 0
        ),
        scored_agents AS (
            SELECT ass.agent_id, ass.final_score, ass.validator_count,
                   COALESCE(vm.validator_hotkeys, ARRAY[]::text[]) AS validator_hotkeys, aiw.disqualified, aiw.created_at,
                   aiw.status AS agent_status
            FROM agent_scores ass
            JOIN agents_in_window aiw ON aiw.agent_id = ass.agent_id
            LEFT JOIN validator_metrics vm ON vm.agent_id = ass.agent_id
            WHERE ass.set_id = $1
            UNION ALL
            SELECT ts.agent_id, ts.final_score, ts.validator_count, ts.validator_hotkeys, ts.disqualified, ts.created_at,
                   ts.agent_status
            FROM tentative_scores ts
        ),
        ranked_scores AS (
            SELECT
                sa.agent_id,
                sa.final_score,
                sa.validator_count,
                vm.average_cost_usd,
                vm.average_runtime_seconds,
                COALESCE(vm.validator_hotkeys, ARRAY[]::text[]) AS validator_hotkeys,
                CASE
                    WHEN sa.disqualified OR sa.agent_status = 'cancelled' THEN NULL
                    ELSE ROW_NUMBER() OVER (
                        PARTITION BY (sa.disqualified OR sa.agent_status = 'cancelled')
                        ORDER BY
                            ROUND(sa.final_score::numeric, 6) DESC,
                            vm.average_cost_usd ASC NULLS LAST,
                            sa.created_at ASC,
                            sa.agent_id ASC
                    )::int
                END AS rank
            FROM scored_agents sa
            LEFT JOIN validator_metrics vm ON vm.agent_id = sa.agent_id
        )
        SELECT
            rs.rank,
            aiw.agent_id,
            aiw.miner_hotkey,
            aiw.name,
            aiw.version_num,
            aiw.status,
            (aa.agent_id IS NOT NULL) AS approved,
            aiw.approval_review_status,
            aa.performance_delta,
            aa.cost_delta,
            aa.relative_improvement_units,
            aa.time_multiplier,
            aa.initial_reward_score,
            baseline.name AS baseline_agent_name,
            baseline.version_num AS baseline_agent_version_num,
            rs.final_score,
            COALESCE(rs.validator_count, 0) AS validator_count,
            rs.average_cost_usd,
            rs.average_runtime_seconds,
            COALESCE(rs.validator_hotkeys, ARRAY[]::text[]) AS validator_hotkeys,
            aiw.created_at,
            aiw.disqualified
        FROM agents_in_window aiw
        LEFT JOIN ranked_scores rs ON rs.agent_id = aiw.agent_id
        LEFT JOIN approved_agents aa
            ON aa.agent_id = aiw.agent_id
           AND aa.set_id = $1
        LEFT JOIN agents baseline ON baseline.agent_id = aa.baseline_agent_id
        ORDER BY
            rs.rank ASC NULLS LAST,
            rs.final_score DESC NULLS LAST,
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
        {_sql_agents_in_window_cte("a.agent_id, a.name, a.version_num, a.created_at, a.status")},
        {_sql_validator_metrics_cte(include_validator_hotkeys=False)},
        {_sql_top_agent_for_summary()},
        efficiency_averages AS (
            SELECT
                AVG(vm.average_cost_usd) AS average_agent_cost_usd,
                AVG(vm.average_runtime_seconds) AS average_agent_runtime_seconds
            FROM validator_metrics vm
            JOIN agents_in_window aa ON vm.agent_id = aa.agent_id
            WHERE NOT aa.disqualified AND aa.status = 'finished'
        ),
        lowest_cost_agent AS (
            SELECT vm.agent_id, vm.average_cost_usd AS value
            FROM validator_metrics vm
            JOIN agents_in_window aa ON vm.agent_id = aa.agent_id
            WHERE NOT aa.disqualified AND aa.status = 'finished'
              AND vm.average_cost_usd IS NOT NULL
            ORDER BY vm.average_cost_usd ASC
            LIMIT 1
        ),
        lowest_runtime_agent AS (
            SELECT vm.agent_id, vm.average_runtime_seconds AS value
            FROM validator_metrics vm
            JOIN agents_in_window aa ON vm.agent_id = aa.agent_id
            WHERE NOT aa.disqualified AND aa.status = 'finished'
              AND vm.average_runtime_seconds IS NOT NULL
            ORDER BY vm.average_runtime_seconds ASC
            LIMIT 1
        )
        SELECT
            ta.agent_id AS top_agent_id,
            ta.name AS top_agent_name,
            ta.version_num AS top_agent_version_num,
            ta.final_score AS top_agent_final_score,
            lca.agent_id AS lowest_cost_agent_id,
            lca.value AS lowest_average_cost_usd_top_agents,
            lra.agent_id AS lowest_runtime_agent_id,
            lra.value AS lowest_average_runtime_seconds_top_agents,
            ea.average_agent_cost_usd,
            ea.average_agent_runtime_seconds
        FROM efficiency_averages ea
        LEFT JOIN top_agent ta ON TRUE
        LEFT JOIN lowest_cost_agent lca ON TRUE
        LEFT JOIN lowest_runtime_agent lra ON TRUE
        """,
        set_id,
    )


@db_operation
async def get_approved_agents_for_set(conn: DatabaseConnection, set_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""
        WITH
        approved_agent_ids AS (
            SELECT aa.agent_id
            FROM approved_agents aa
            WHERE aa.set_id = $1
              AND aa.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        ),
        {_sql_validator_metrics_cte(include_validator_hotkeys=False, agent_filter_cte="approved_agent_ids")}
        -- agent_scores has one row per agent; set_id tracks the most recent set.
        -- Agents approved for this set but re-scored in a later set will not appear here.
        SELECT
            a.agent_id,
            a.miner_hotkey,
            a.name,
            a.version_num,
            a.created_at,
            ass.final_score,
            aa.approved_at,
            vm.average_cost_usd,
            vm.average_runtime_seconds
        FROM approved_agents aa
        JOIN agents a ON a.agent_id = aa.agent_id
        JOIN agent_scores ass ON ass.agent_id = aa.agent_id AND ass.set_id = $1
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = aa.agent_id
            AND review.set_id = aa.set_id
        LEFT JOIN validator_metrics vm ON vm.agent_id = aa.agent_id
        WHERE aa.set_id = $1
          AND aa.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND NOT EXISTS (
              SELECT 1
              FROM banned_coldkeys bc
              WHERE bc.miner_coldkey = a.miner_coldkey
          )
          AND ass.status = 'finished'
          AND review.approval_review_status is distinct from 'rejected'
        ORDER BY aa.approved_at DESC
        """,
        set_id,
    )
