import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

import api.config as config
from models.agent import AgentStatus
from models.evaluation import Evaluation, EvaluationStatus, HydratedEvaluation
from models.evaluation_run import EvaluationRun, EvaluationRunErrorCode, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup
from queries.evaluation_run import create_evaluation_runs, get_all_evaluation_runs_in_evaluation_id
from queries.evaluation_set import get_all_evaluation_set_problems_in_set_group_in_set_id, get_latest_set_id
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)

# Copies terminal error state from just-updated evaluation_runs mirrors onto their
# current attempts. $1 narrowing differs per caller; see usages below.
_MIRROR_ERRORED_RUNS_TO_CURRENT_ATTEMPTS = """
    UPDATE evaluation_run_attempts era
    SET
        status = er.status,
        error_code = er.error_code,
        error_message = er.error_message,
        finished_or_errored_at = er.finished_or_errored_at
    FROM evaluation_runs er
    WHERE er.evaluation_run_id = era.evaluation_run_id
      AND er.status = 'error'
      AND era.attempt_number = (
          SELECT MAX(a2.attempt_number) FROM evaluation_run_attempts a2
          WHERE a2.evaluation_run_id = era.evaluation_run_id
      )
      AND era.status NOT IN ('finished', 'error')
"""


@dataclass(slots=True, frozen=True)
class LocalEvaluationScoreBound:
    total_runs: int
    impossible_runs: int
    upper_bound: float


@db_operation
async def create_evaluation(conn: DatabaseConnection, agent_id: UUID, validator_hotkey: str, set_id: int) -> UUID:
    evaluation_id = uuid4()

    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            created_at,
            evaluation_set_group
        ) VALUES ($1, $2, $3, $4, NOW(), $5)
        """,
        evaluation_id,
        agent_id,
        validator_hotkey,
        set_id,
        EvaluationSetGroup.from_validator_hotkey(validator_hotkey).value,
    )

    logger.debug(
        f"Created evaluation {evaluation_id} for agent {agent_id} with validator hotkey {validator_hotkey} and set ID {set_id}"
    )

    return evaluation_id


@db_operation
async def create_new_evaluation_and_evaluation_runs(
    conn: DatabaseConnection, agent_id: UUID, validator_hotkey: str, set_id: int = None
) -> Optional[Tuple[Evaluation, List[EvaluationRun]]]:
    if set_id is None:
        set_id = await get_latest_set_id()
        if set_id is None:
            logger.info(
                f"Skipping evaluation issuance for agent {agent_id}: no Harbor evaluation set has been promoted yet"
            )
            return None

    logger.debug(
        f"Creating new evaluation and evaluation runs for agent {agent_id} with validator hotkey {validator_hotkey} and set ID {set_id}"
    )

    set_group = EvaluationSetGroup.from_validator_hotkey(validator_hotkey)
    evaluation_set_problems = await get_all_evaluation_set_problems_in_set_group_in_set_id(set_id, set_group)
    if not evaluation_set_problems:
        logger.info(
            f"Skipping evaluation issuance for agent {agent_id}: set_id {set_id} has no tasks for {set_group.value}"
        )
        return None

    logger.debug(f"# of problems in set ID {set_id}, set group {set_group.value}: {len(evaluation_set_problems)}")

    evaluation_id = await create_evaluation(agent_id, validator_hotkey, set_id)

    await create_evaluation_runs(evaluation_id, evaluation_set_problems)

    return await get_evaluation_by_id(evaluation_id), await get_all_evaluation_runs_in_evaluation_id(evaluation_id)


@db_operation
async def get_evaluation_by_id(conn: DatabaseConnection, evaluation_id: UUID) -> Evaluation:
    response = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    return Evaluation(**response)


@db_operation
async def get_hydrated_evaluation_by_id(conn: DatabaseConnection, evaluation_id: UUID) -> Optional[HydratedEvaluation]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations_hydrated
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    if result is None:
        return None

    return HydratedEvaluation(**result)


@db_operation
async def get_hydrated_evaluation_by_evaluation_run_id(
    conn: DatabaseConnection, evaluation_run_id: UUID
) -> Optional[HydratedEvaluation]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations_hydrated
        WHERE evaluation_id = (SELECT evaluation_id FROM evaluation_runs WHERE evaluation_run_id = $1 LIMIT 1)
        """,
        evaluation_run_id,
    )

    if result is None:
        return None

    return HydratedEvaluation(**result)


@db_operation
async def get_evaluations_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> List[Evaluation]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluations
        WHERE agent_id = $1
        """,
        agent_id,
    )

    return [Evaluation(**evaluation) for evaluation in results]


@db_operation
async def update_evaluation_finished_at(conn: DatabaseConnection, evaluation_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE evaluations
        SET finished_at = NOW()
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )


@db_operation
async def get_num_successful_validator_evaluations_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> int:
    return await conn.fetchval(
        f"""
        SELECT COUNT(*)
        FROM evaluations_hydrated
        WHERE 
            agent_id = $1
            AND status = '{EvaluationStatus.success.value}'
            AND evaluation_set_group = '{EvaluationSetGroup.validator.value}'::EvaluationSetGroup
        """,
        agent_id,
    )


@db_operation
async def get_approved_validator_leader_score_for_set(
    conn: DatabaseConnection,
    set_id: int,
    excluded_agent_id: UUID,
    required_validator_count: int = config.NUM_EVALS_PER_AGENT,
) -> Optional[float]:
    return await conn.fetchval(
        """
        SELECT MAX(agent_score.final_score)
        FROM agent_scores agent_score
        INNER JOIN agents agent ON agent.agent_id = agent_score.agent_id
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = agent_score.agent_id
            AND review.set_id = agent_score.set_id
        WHERE agent_score.set_id = $1
          AND agent_score.approved IS TRUE
          AND agent_score.approved_at <= NOW()
          AND agent_score.validator_count = $2
          AND agent_score.status::text = 'finished'
          AND agent_score.agent_id <> $3
          AND review.approval_review_status IS DISTINCT FROM 'rejected'
          AND NOT EXISTS (
              SELECT 1
              FROM benchmark_agent_ids benchmark_agent
              WHERE benchmark_agent.agent_id = agent_score.agent_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM banned_coldkeys banned_coldkey
              WHERE banned_coldkey.miner_coldkey = agent.miner_coldkey
          )
        """,
        set_id,
        required_validator_count,
        excluded_agent_id,
    )


@dataclass(slots=True, frozen=True)
class AgentRankingProfile:
    final_score: float
    avg_cost_usd: Optional[float]
    created_at: datetime
    agent_id: UUID | None = None
    approved_at: datetime | None = None
    miner_coldkey: str | None = None
    observed_at: datetime | None = None

    def beats(self, other: "AgentRankingProfile") -> bool:
        """Check if an AgentRankingProfile instance can beat
        another one.

        It checks the following parameters in order:
        - Final score
        - Average cost throughout all eval runs
        - Agent creation datetime

        Parameters
        ----------
        other : AgentRankingProfile
            Another AgentRankingProfile instance.
        Returns
        -------
        bool
            True if this instance beats the `other`, False if not.
        """
        if self.final_score > other.final_score:
            return True
        if self.final_score < other.final_score:
            return False

        s_cost = self.avg_cost_usd
        o_cost = other.avg_cost_usd
        if s_cost is not None and o_cost is not None:
            if s_cost < o_cost:
                return True
            if s_cost > o_cost:
                return False
        elif s_cost is not None and o_cost is None:
            return True
        elif s_cost is None and o_cost is not None:
            return False

        if self.created_at < other.created_at:
            return True

        return False


@db_operation
async def get_approved_leader_ranking_for_set(
    conn: DatabaseConnection,
    set_id: int,
    excluded_agent_id: UUID | None = None,
    required_validator_count: int = config.NUM_EVALS_PER_AGENT,
) -> Optional[AgentRankingProfile]:
    row = await conn.fetchrow(
        """
        SELECT
            ass.agent_id,
            ass.final_score,
            rt.avg_cost_usd,
            ass.created_at,
            ass.approved_at,
            agent.miner_coldkey,
            clock_timestamp() AS observed_at
        FROM agent_scores ass
        INNER JOIN agents agent ON agent.agent_id = ass.agent_id
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = ass.agent_id
            AND review.set_id = ass.set_id
        LEFT JOIN LATERAL (
            SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
            FROM evaluations_hydrated eh
            WHERE eh.agent_id             = ass.agent_id
              AND eh.set_id               = ass.set_id
              AND eh.evaluation_set_group  = 'validator'::EvaluationSetGroup
              AND eh.status               = 'success'::EvaluationStatus
        ) rt ON true
        WHERE ass.set_id = $1
          AND ass.approved IS TRUE
          AND ass.approved_at <= clock_timestamp()
          AND ass.validator_count = $2
          AND ass.status::text = 'finished'
          AND ($3::uuid IS NULL OR ass.agent_id <> $3)
          AND review.approval_review_status IS DISTINCT FROM 'rejected'
          AND NOT EXISTS (
              SELECT 1
              FROM benchmark_agent_ids benchmark_agent
              WHERE benchmark_agent.agent_id = ass.agent_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM banned_coldkeys banned_coldkey
              WHERE banned_coldkey.miner_coldkey = agent.miner_coldkey
          )
        ORDER BY ass.final_score DESC, rt.avg_cost_usd ASC NULLS LAST, ass.created_at ASC
        LIMIT 1
        """,
        set_id,
        required_validator_count,
        excluded_agent_id,
    )
    if row is None:
        return None
    return AgentRankingProfile(
        final_score=row["final_score"],
        avg_cost_usd=row["avg_cost_usd"],
        created_at=row["created_at"],
        agent_id=row["agent_id"],
        approved_at=row["approved_at"],
        miner_coldkey=row["miner_coldkey"],
        observed_at=row["observed_at"],
    )


@db_operation
async def get_validator_agent_score_for_set(
    conn: DatabaseConnection,
    agent_id: UUID,
    set_id: int,
    required_validator_count: int = config.NUM_EVALS_PER_AGENT,
) -> Optional[AgentRankingProfile]:
    row = await conn.fetchrow(
        """
        SELECT
            ass.agent_id,
            ass.final_score,
            rt.avg_cost_usd,
            ass.created_at
        FROM agent_scores ass
        LEFT JOIN LATERAL (
            SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
            FROM evaluations_hydrated eh
            WHERE eh.agent_id             = ass.agent_id
              AND eh.set_id               = ass.set_id
              AND eh.evaluation_set_group  = 'validator'::EvaluationSetGroup
              AND eh.status               = 'success'::EvaluationStatus
        ) rt ON true
        WHERE ass.agent_id = $1
          AND ass.set_id = $2
          AND ass.validator_count = $3
          AND ass.status::text <> 'cancelled'
          AND NOT EXISTS (
              SELECT 1
              FROM benchmark_agent_ids benchmark_agent
              WHERE benchmark_agent.agent_id = ass.agent_id
          )
        LIMIT 1
        """,
        agent_id,
        set_id,
        required_validator_count,
    )
    if row is None:
        return None
    return AgentRankingProfile(
        final_score=row["final_score"],
        avg_cost_usd=row["avg_cost_usd"],
        created_at=row["created_at"],
        agent_id=row["agent_id"],
    )


@db_operation
async def get_local_evaluation_score_upper_bound(
    conn: DatabaseConnection, evaluation_id: UUID
) -> Optional[LocalEvaluationScoreBound]:
    row = await conn.fetchrow(
        f"""
        SELECT
            COUNT(*)::int AS total_runs,
            COUNT(*) FILTER (
                WHERE
                    (
                        status = '{EvaluationRunStatus.finished.value}'::evaluationrunstatus
                        AND solved IS NOT TRUE
                    )
                    OR (
                        status = '{EvaluationRunStatus.error.value}'::evaluationrunstatus
                        AND error_code >= 1000
                        AND error_code < 2000
                    )
            )::int AS impossible_runs
        FROM evaluation_runs_hydrated
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    if row is None or row["total_runs"] == 0:
        return None

    total_runs = row["total_runs"]
    impossible_runs = row["impossible_runs"]
    return LocalEvaluationScoreBound(
        total_runs=total_runs,
        impossible_runs=impossible_runs,
        upper_bound=(total_runs - impossible_runs) / total_runs,
    )


@db_operation
async def transition_agent_status_if_matches(
    conn: DatabaseConnection,
    agent_id: UUID,
    expected_status: AgentStatus,
    new_status: AgentStatus,
) -> bool:
    result = await conn.execute(
        """
        UPDATE agents
        SET status = $3
        WHERE agent_id = $1
          AND status = $2
        """,
        agent_id,
        expected_status.value,
        new_status.value,
    )
    return result == "UPDATE 1"


@db_operation
async def set_unfinished_evaluation_runs_to_score_pruned(
    conn: DatabaseConnection, evaluation_id: UUID, error_message: str
) -> int:
    async with conn.conn.transaction():
        count = await conn.fetchval(
            f"""
            WITH updated AS (
                UPDATE evaluation_runs
                SET
                    status = '{EvaluationRunStatus.error.value}',
                    error_code = {EvaluationRunErrorCode.PLATFORM_PRUNED_BY_SCORE_BOUND.value},
                    error_message = $2,
                    finished_or_errored_at = NOW()
                WHERE evaluation_id = $1
                  AND status NOT IN (
                      '{EvaluationRunStatus.finished.value}'::evaluationrunstatus,
                      '{EvaluationRunStatus.error.value}'::evaluationrunstatus
                  )
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM updated
            """,
            evaluation_id,
            error_message,
        )

        await conn.execute(
            _MIRROR_ERRORED_RUNS_TO_CURRENT_ATTEMPTS + " AND er.evaluation_id = $1",
            evaluation_id,
        )

    return count


@db_operation
async def set_all_unfinished_evaluation_runs_to_errored(conn: DatabaseConnection, error_message: str) -> None:
    async with conn.conn.transaction():
        await conn.execute(
            f"""
            UPDATE evaluation_runs
            SET
                status = '{EvaluationRunStatus.error.value}',
                error_code = CASE
                    WHEN status = '{EvaluationRunStatus.pending.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_PENDING.value}
                    WHEN status = '{EvaluationRunStatus.initializing_agent.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT.value}
                    WHEN status = '{EvaluationRunStatus.running_agent.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT.value}
                    WHEN status = '{EvaluationRunStatus.initializing_eval.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL.value}
                    WHEN status = '{EvaluationRunStatus.running_eval.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL.value}
                    ELSE {EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value}
                END,
                error_message = $1,
                finished_or_errored_at = NOW()
            WHERE
                status NOT IN ('{EvaluationRunStatus.finished.value}', '{EvaluationRunStatus.error.value}')
            """,
            error_message,
        )

        await conn.execute(_MIRROR_ERRORED_RUNS_TO_CURRENT_ATTEMPTS)


@db_operation
async def update_unfinished_evaluation_runs_in_evaluation_id_to_errored(
    conn: DatabaseConnection, evaluation_id: UUID, error_message: str
) -> None:
    async with conn.conn.transaction():
        await conn.execute(
            f"""
            UPDATE evaluation_runs
            SET
                status = '{EvaluationRunStatus.error.value}',
                error_code = CASE
                    WHEN status = '{EvaluationRunStatus.pending.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING.value}
                    WHEN status = '{EvaluationRunStatus.initializing_agent.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_AGENT.value}
                    WHEN status = '{EvaluationRunStatus.running_agent.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_AGENT.value}
                    WHEN status = '{EvaluationRunStatus.initializing_eval.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_EVAL.value}
                    WHEN status = '{EvaluationRunStatus.running_eval.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_EVAL.value}
                END,
                error_message = $2,
                finished_or_errored_at = NOW()
            WHERE evaluation_id = $1
            AND status NOT IN ('{EvaluationRunStatus.finished.value}', '{EvaluationRunStatus.error.value}')
            """,
            evaluation_id,
            error_message,
        )

        await conn.execute(
            _MIRROR_ERRORED_RUNS_TO_CURRENT_ATTEMPTS + " AND er.evaluation_id = $1",
            evaluation_id,
        )
