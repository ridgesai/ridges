import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid5

import api.config as config
from models.agent import (
    Agent,
    AgentCreate,
    AgentStatus,
    PublicAgent,
)
from models.competition import CompetitionState
from models.evaluation import EvaluationStatus
from models.evaluation_set import EvaluationSetGroup
from models.queue import QueueStage
from queries.banned_coldkey import get_banned_coldkey, lock_coldkey_ban_state
from queries.competition import (
    get_current_competition_context,
    lock_competition_for_admission,
    resolve_upload_competition,
)
from queries.errors import (
    ColdkeyBannedError,
    CompetitionNotAcceptingSubmissionsError,
    DuplicateAgentIDError,
    UploadCooldownError,
    UploadCreditAlreadyRedeemedError,
    UploadCreditUnavailableError,
    UploadFundingConflictError,
)
from utils.agent_secrets import decrypt_agent_secret
from utils.database import DatabaseConnection, db_operation
from utils.s3 import upload_text_file_to_s3

logger = logging.getLogger(__name__)

UPLOAD_HOTKEY_LOCK_NAMESPACE = -1730
UPLOAD_SOURCE_LOCK_NAMESPACE = -1731


@dataclass(slots=True, frozen=True)
class AgentOpenRouterSecrets:
    runtime_api_key: str
    management_api_key: str
    workspace_id: str
    api_key_label: str
    api_key_creator_user_id: str
    validated_at: datetime


@dataclass(slots=True, frozen=True)
class BurnUploadFunding:
    payment_block_hash: str
    payment_extrinsic_index: str
    miner_hotkey: str
    miner_coldkey: str
    amount_alpha_rao: int
    quote_id: UUID


@dataclass(slots=True, frozen=True)
class CreditUploadFunding:
    credit_id: UUID
    miner_hotkey: str
    miner_coldkey: str


@dataclass(slots=True, frozen=True)
class AgentAdmissionResult:
    agent_id: UUID
    replayed: bool = False
    miner_coldkey: str | None = None


@dataclass(slots=True, frozen=True)
class EvaluationCandidate:
    agent_id: UUID
    set_id: int


@dataclass(slots=True, frozen=True)
class EvaluationCandidateBatch:
    observed_last_served_set_id: int | None
    candidates: tuple[EvaluationCandidate, ...]


def _derive_agent_id(payment_block_hash: str, payment_extrinsic_index: str) -> UUID:
    return uuid5(
        config.AGENT_UUID_NAMESPACE,
        f"{payment_block_hash}:{payment_extrinsic_index}",
    )


@db_operation
async def get_agent_by_id(conn: DatabaseConnection, agent_id: UUID) -> Optional[Agent]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM agents 
        WHERE agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if result is None:
        return None

    return Agent(**result)


AGENT_PUBLIC_JOINS = """
LEFT JOIN LATERAL (
    SELECT COALESCE(
        a.set_id,
        (
            SELECT aa.set_id
            FROM approved_agents aa
            WHERE aa.agent_id = a.agent_id
            ORDER BY aa.set_id DESC
            LIMIT 1
        ),
        (
            SELECT afr.set_id
            FROM agent_final_review_statuses afr
            WHERE afr.agent_id = a.agent_id
            ORDER BY afr.updated_at DESC, afr.set_id DESC
            LIMIT 1
        ),
        (SELECT ass.set_id FROM agent_scores ass WHERE ass.agent_id = a.agent_id),
        (SELECT MAX(e.set_id) FROM evaluations e WHERE e.agent_id = a.agent_id)
    ) AS set_id
) competition_context ON TRUE
LEFT JOIN agent_final_review_statuses competition_review
    ON competition_review.agent_id = a.agent_id
   AND competition_review.set_id = competition_context.set_id
LEFT JOIN agent_scores competition_score
    ON competition_score.agent_id = a.agent_id
   AND competition_score.set_id = competition_context.set_id
LEFT JOIN approved_agents competition_approval
    ON competition_approval.agent_id = a.agent_id
   AND competition_approval.set_id = competition_context.set_id
LEFT JOIN agents competition_baseline
    ON competition_baseline.agent_id = competition_approval.baseline_agent_id
   AND (
       competition_baseline.set_id IS NULL
       OR competition_baseline.set_id = competition_context.set_id
   )
"""

AGENT_PUBLIC_EXPLICIT_JOINS = """
LEFT JOIN LATERAL (
    SELECT $2::integer AS set_id
) competition_context ON TRUE
LEFT JOIN agent_final_review_statuses competition_review
    ON competition_review.agent_id = a.agent_id
   AND competition_review.set_id = competition_context.set_id
LEFT JOIN agent_scores competition_score
    ON competition_score.agent_id = a.agent_id
   AND competition_score.set_id = competition_context.set_id
LEFT JOIN approved_agents competition_approval
    ON competition_approval.agent_id = a.agent_id
   AND competition_approval.set_id = competition_context.set_id
LEFT JOIN agents competition_baseline
    ON competition_baseline.agent_id = competition_approval.baseline_agent_id
   AND (
       competition_baseline.set_id IS NULL
       OR competition_baseline.set_id = competition_context.set_id
   )
"""

AGENT_PUBLIC_SELECT_COLUMNS = """
    a.agent_id,
    a.miner_hotkey,
    a.miner_coldkey,
    a.name,
    a.version_num,
    a.status,
    a.created_at,
    (a.set_id IS NULL) AS legacy_membership,
    competition_context.set_id,
    competition_score.validator_count,
    competition_score.final_score,
    competition_review.approval_review_status,
    (competition_approval.agent_id IS NOT NULL) AS approved,
    competition_approval.performance_delta,
    competition_approval.cost_delta,
    competition_approval.relative_improvement_units,
    competition_approval.time_multiplier,
    competition_approval.initial_reward_score,
    competition_approval.approved_at,
    competition_baseline.agent_id AS baseline_agent_id,
    competition_baseline.name AS baseline_agent_name,
    competition_baseline.version_num AS baseline_agent_version_num
"""

LATEST_AGENT_REVIEW_JOIN = """
LEFT JOIN LATERAL (
    SELECT approval_review_status
    FROM agent_final_review_statuses
    WHERE agent_final_review_statuses.agent_id = a.agent_id
    ORDER BY agent_final_review_statuses.updated_at DESC, agent_final_review_statuses.set_id DESC
    LIMIT 1
) latest_review ON TRUE
"""


@db_operation
async def get_public_agent_by_id(conn: DatabaseConnection, agent_id: UUID) -> PublicAgent | None:
    result = await conn.fetchrow(
        f"""
        SELECT
            {AGENT_PUBLIC_SELECT_COLUMNS}
        FROM agents a
        {AGENT_PUBLIC_JOINS}
        WHERE a.agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if result is None:
        return None

    return PublicAgent(**result)


@db_operation
async def get_agent_by_evaluation_run_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> PublicAgent | None:
    result = await conn.fetchrow(
        f"""
        SELECT
            {AGENT_PUBLIC_SELECT_COLUMNS}
        FROM agents a
        {AGENT_PUBLIC_JOINS}
        WHERE a.agent_id = (
            SELECT agent_id FROM evaluations WHERE evaluation_id = (
                SELECT evaluation_id FROM evaluation_runs WHERE evaluation_run_id = $1 LIMIT 1
            ) LIMIT 1
        )
        """,
        evaluation_run_id,
    )

    if result is None:
        return None

    return PublicAgent(**result)


@db_operation
async def get_all_public_agents_by_miner_hotkey(
    conn: DatabaseConnection,
    miner_hotkey: str,
    set_id: int | None = None,
) -> list[PublicAgent]:
    if set_id is None:
        result = await conn.fetch(
            f"""
            SELECT
                {AGENT_PUBLIC_SELECT_COLUMNS}
            FROM agents a
            {AGENT_PUBLIC_JOINS}
            WHERE a.miner_hotkey = $1
            ORDER BY a.created_at DESC
            """,
            miner_hotkey,
        )
    else:
        result = await conn.fetch(
            f"""
            SELECT
                {AGENT_PUBLIC_SELECT_COLUMNS}
            FROM agents a
            {AGENT_PUBLIC_EXPLICIT_JOINS}
            WHERE a.miner_hotkey = $1
              AND (
                  a.set_id = $2
                  OR (
                      a.set_id IS NULL
                      AND (
                          EXISTS (
                              SELECT 1 FROM evaluations evidence
                              WHERE evidence.agent_id = a.agent_id AND evidence.set_id = $2
                          )
                          OR EXISTS (
                              SELECT 1 FROM agent_scores evidence
                              WHERE evidence.agent_id = a.agent_id AND evidence.set_id = $2
                          )
                          OR EXISTS (
                              SELECT 1 FROM approved_agents evidence
                              WHERE evidence.agent_id = a.agent_id AND evidence.set_id = $2
                          )
                          OR EXISTS (
                              SELECT 1 FROM agent_final_review_statuses evidence
                              WHERE evidence.agent_id = a.agent_id AND evidence.set_id = $2
                          )
                      )
                  )
              )
            ORDER BY a.created_at DESC
            """,
            miner_hotkey,
            set_id,
        )

    return [PublicAgent(**agent) for agent in result]


@db_operation
async def get_latest_agent_for_miner_hotkey(conn: DatabaseConnection, miner_hotkey: str) -> Optional[Agent]:
    """Return the core latest-agent model used by upload/platform machinery."""
    result = await conn.fetchrow(
        f"""
        SELECT
            a.*,
            latest_review.approval_review_status AS approval_review_status
        FROM agents a
        {LATEST_AGENT_REVIEW_JOIN}
        WHERE a.miner_hotkey = $1
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        miner_hotkey,
    )

    if result is None:
        return None

    return Agent(**result)


@db_operation
async def get_latest_public_agent_for_miner_hotkey(conn: DatabaseConnection, miner_hotkey: str) -> PublicAgent | None:
    """Return the latest agent enriched for public competition views."""
    result = await conn.fetchrow(
        f"""
        SELECT
            {AGENT_PUBLIC_SELECT_COLUMNS}
        FROM agents a
        {AGENT_PUBLIC_JOINS}
        WHERE a.miner_hotkey = $1
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        miner_hotkey,
    )

    if result is None:
        return None

    return PublicAgent(**result)


@db_operation
async def get_latest_agent_created_at_for_miner_hotkey_in_current_competition(
    conn: DatabaseConnection, miner_hotkey: str
) -> Optional[datetime]:
    result = await conn.fetchval(
        """
        WITH current_competition AS (
            SELECT set_id
            FROM competitions
            WHERE start_date IS NOT NULL
            ORDER BY set_id DESC
            LIMIT 1
        )
        SELECT MAX(a.created_at)
        FROM agents a
        JOIN current_competition current ON current.set_id = a.set_id
        WHERE a.miner_hotkey = $1
        """,
        miner_hotkey,
    )

    return result


@db_operation
async def get_latest_agent_created_at_for_miner_hotkey_in_competition(
    conn: DatabaseConnection,
    miner_hotkey: str,
    set_id: int,
) -> Optional[datetime]:
    return await conn.fetchval(
        """
        SELECT MAX(created_at)
        FROM agents
        WHERE miner_hotkey = $1
          AND set_id = $2
        """,
        miner_hotkey,
        set_id,
    )


@db_operation
async def create_agent(
    conn: DatabaseConnection,
    agent: AgentCreate,
    agent_text: str,
    *,
    source_sha256: str,
    runtime_openrouter_api_key_ciphertext: bytes,
    management_openrouter_api_key_ciphertext: bytes,
    openrouter_workspace_id: str,
    openrouter_api_key_label: str,
    openrouter_api_key_creator_user_id: str,
    openrouter_validated_at: datetime,
    miner_coldkey: Optional[str] = None,
    set_id: int | None = None,
) -> UUID:
    """Create an unfunded dev/owner agent through the admission transaction."""

    if set_id is None:
        current = await get_current_competition_context()
        if current is None:
            raise CompetitionNotAcceptingSubmissionsError(set_id=None, state=None)
        resolved_set_id = current.set_id
    else:
        resolved_set_id = await resolve_upload_competition(set_id)

    agent_id = _derive_agent_id(agent.payment_block_hash, agent.payment_extrinsic_index)
    await upload_text_file_to_s3(f"{agent_id}/agent.py", agent_text)

    result = await admit_agent(
        agent,
        set_id=resolved_set_id,
        source_sha256=source_sha256,
        runtime_openrouter_api_key_ciphertext=runtime_openrouter_api_key_ciphertext,
        management_openrouter_api_key_ciphertext=management_openrouter_api_key_ciphertext,
        openrouter_workspace_id=openrouter_workspace_id,
        openrouter_api_key_label=openrouter_api_key_label,
        openrouter_api_key_creator_user_id=openrouter_api_key_creator_user_id,
        openrouter_validated_at=openrouter_validated_at,
        miner_coldkey=miner_coldkey,
        funding=None,
        enforce_cooldown=False,
    )
    return result.agent_id


async def _lock_burn_funding(conn: DatabaseConnection, funding: BurnUploadFunding) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_payments (
            payment_block_hash,
            payment_extrinsic_index,
            agent_id,
            miner_hotkey,
            miner_coldkey,
            amount_alpha_rao,
            quote_id
        ) VALUES ($1, $2, NULL, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        funding.payment_block_hash,
        funding.payment_extrinsic_index,
        funding.miner_hotkey,
        funding.miner_coldkey,
        funding.amount_alpha_rao,
        funding.quote_id,
    )
    row = await conn.fetchrow(
        """
        SELECT *
        FROM evaluation_payments
        WHERE payment_block_hash = $1
          AND payment_extrinsic_index = $2
        FOR UPDATE
        """,
        funding.payment_block_hash,
        funding.payment_extrinsic_index,
    )
    if row is None:
        raise UploadFundingConflictError()

    if row["agent_id"] is not None:
        raise DuplicateAgentIDError(row["agent_id"])

    if (
        row["miner_hotkey"] != funding.miner_hotkey
        or row["miner_coldkey"] != funding.miner_coldkey
        or row["amount_alpha_rao"] != funding.amount_alpha_rao
        or row["amount_rao"] is not None
        or row["quote_id"] != funding.quote_id
        or row["upload_credit_id"] is not None
    ):
        raise UploadFundingConflictError()


async def _lock_credit_funding(
    conn: DatabaseConnection,
    funding: CreditUploadFunding,
    *,
    source_sha256: str,
    set_id: int,
) -> AgentAdmissionResult | None:
    credit = await conn.fetchrow(
        """
        SELECT *, expires_at IS NOT NULL AND expires_at <= clock_timestamp() AS is_expired
        FROM upload_credits
        WHERE credit_id = $1
          AND miner_hotkey = $2
        FOR UPDATE
        """,
        funding.credit_id,
        funding.miner_hotkey,
    )
    if credit is None or credit["revoked_at"] is not None:
        raise UploadCreditUnavailableError()

    if credit["redeemed_at"] is None and credit["is_expired"]:
        raise UploadCreditUnavailableError()

    if credit["redeemed_agent_id"] is None:
        return None

    existing = await conn.fetchrow(
        """
        SELECT miner_hotkey, miner_coldkey, source_sha256, set_id
        FROM agents
        WHERE agent_id = $1
        """,
        credit["redeemed_agent_id"],
    )
    if (
        existing is not None
        and existing["miner_hotkey"] == funding.miner_hotkey
        and existing["source_sha256"] == source_sha256
        and existing["set_id"] == set_id
    ):
        return AgentAdmissionResult(
            agent_id=credit["redeemed_agent_id"],
            replayed=True,
            miner_coldkey=existing["miner_coldkey"],
        )
    raise UploadCreditAlreadyRedeemedError(credit["redeemed_agent_id"])


async def _selected_set_name_and_version(
    conn: DatabaseConnection,
    *,
    set_id: int,
    miner_hotkey: str,
    requested_name: str,
) -> tuple[str, int]:
    row = await conn.fetchrow(
        """
        SELECT
            MAX(version_num) AS max_version,
            (
                SELECT existing.name
                FROM agents existing
                WHERE existing.set_id = $1
                  AND existing.miner_hotkey = $2
                ORDER BY existing.version_num DESC, existing.created_at DESC, existing.agent_id
                LIMIT 1
            ) AS current_name
        FROM agents
        WHERE set_id = $1
          AND miner_hotkey = $2
        """,
        set_id,
        miner_hotkey,
    )
    if row["max_version"] is None:
        return requested_name, 0
    return row["current_name"], int(row["max_version"]) + 1


@db_operation
async def admit_agent(
    conn: DatabaseConnection,
    agent: AgentCreate,
    *,
    set_id: int,
    source_sha256: str,
    runtime_openrouter_api_key_ciphertext: bytes,
    management_openrouter_api_key_ciphertext: bytes,
    openrouter_workspace_id: str,
    openrouter_api_key_label: str,
    openrouter_api_key_creator_user_id: str,
    openrouter_validated_at: datetime,
    miner_coldkey: Optional[str],
    funding: BurnUploadFunding | CreditUploadFunding | None,
    enforce_cooldown: bool,
) -> AgentAdmissionResult:
    """Atomically bind a fresh upload to one competition and consume its funding."""

    from queries.pre_screening_judge import (
        duplicate_source_result,
        insert_pending_pre_screening_job,
        insert_terminal_pre_screening_job_with_result,
    )

    agent_id = _derive_agent_id(agent.payment_block_hash, agent.payment_extrinsic_index)

    async with conn.conn.transaction():
        competition = await lock_competition_for_admission(conn, set_id)
        if competition is None:
            raise CompetitionNotAcceptingSubmissionsError(set_id=set_id, state=None)

        if competition.state is not CompetitionState.open:
            raise CompetitionNotAcceptingSubmissionsError(
                set_id=competition.set_id,
                state=competition.state.value,
            )

        if competition.policy is None:
            raise CompetitionNotAcceptingSubmissionsError(set_id=competition.set_id, state=None)

        policy = competition.policy
        initial_status = AgentStatus.pre_screening if policy.pre_screening_enabled else AgentStatus.screening_1

        replay: AgentAdmissionResult | None = None
        if isinstance(funding, BurnUploadFunding):
            await _lock_burn_funding(conn, funding)

        elif isinstance(funding, CreditUploadFunding):
            replay = await _lock_credit_funding(
                conn,
                funding,
                source_sha256=source_sha256,
                set_id=set_id,
            )

        if replay is not None:
            return replay

        await conn.execute(
            "SELECT pg_advisory_xact_lock($1, hashtext($2))",
            UPLOAD_HOTKEY_LOCK_NAMESPACE,
            f"{set_id}:{agent.miner_hotkey}",
        )
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1, hashtext($2))",
            UPLOAD_SOURCE_LOCK_NAMESPACE,
            f"{set_id}:{source_sha256}",
        )

        if miner_coldkey is not None:
            await lock_coldkey_ban_state(conn, miner_coldkey)
            if await get_banned_coldkey(miner_coldkey) is not None:
                raise ColdkeyBannedError(miner_coldkey)

        if enforce_cooldown:
            latest_created_at = await conn.fetchval(
                """
                SELECT MAX(created_at)
                FROM agents
                WHERE set_id = $1
                  AND miner_hotkey = $2
                """,
                set_id,
                agent.miner_hotkey,
            )
            now = await conn.fetchval("SELECT clock_timestamp()")
            if latest_created_at is not None and now < latest_created_at + timedelta(
                seconds=config.MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
            ):
                raise UploadCooldownError(latest_created_at)

        selected_name, version_num = await _selected_set_name_and_version(
            conn,
            set_id=set_id,
            miner_hotkey=agent.miner_hotkey,
            requested_name=agent.name,
        )
        duplicate_agent_id = await conn.fetchval(
            """
            SELECT agent_id
            FROM agents
            WHERE set_id = $1
              AND source_sha256 = $2
            ORDER BY created_at ASC, agent_id ASC
            LIMIT 1
            """,
            set_id,
            source_sha256,
        )

        result = await conn.fetchval(
            """
            INSERT INTO agents (
                agent_id,
                miner_hotkey,
                miner_coldkey,
                name,
                version_num,
                created_at,
                status,
                ip_address,
                source_sha256,
                set_id
            )
            VALUES ($1, $2, $3, $4, $5, clock_timestamp(), $6, $7, $8, $9)
            ON CONFLICT (agent_id) DO NOTHING
            RETURNING agent_id
            """,
            agent_id,
            agent.miner_hotkey,
            miner_coldkey,
            selected_name,
            version_num,
            initial_status.value,
            agent.ip_address,
            source_sha256,
            set_id,
        )

        if result is None:
            raise DuplicateAgentIDError(agent_id)

        # 4. Insert OpenRouter secrets into the database
        await conn.execute(
            """
            INSERT INTO agent_openrouter_secrets (
                agent_id,
                runtime_api_key_ciphertext,
                management_api_key_ciphertext,
                workspace_id,
                api_key_label,
                api_key_creator_user_id,
                validated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            agent_id,
            runtime_openrouter_api_key_ciphertext,
            management_openrouter_api_key_ciphertext,
            openrouter_workspace_id,
            openrouter_api_key_label,
            openrouter_api_key_creator_user_id,
            openrouter_validated_at,
        )

        # 5. Optionally create a pre-screening job for the agent
        if policy.pre_screening_enabled:
            if duplicate_agent_id is not None:
                await insert_terminal_pre_screening_job_with_result(
                    conn,
                    agent_id=agent_id,
                    set_id=set_id,
                    policy_version=policy.hardcoding_policy_version,
                    job_status="failed",
                    result=duplicate_source_result(
                        policy_version=policy.hardcoding_policy_version,
                        matched_agent_id=duplicate_agent_id,
                    ),
                )
            else:
                await insert_pending_pre_screening_job(
                    conn,
                    agent_id=agent_id,
                    set_id=set_id,
                    policy_version=policy.hardcoding_policy_version,
                )

        if isinstance(funding, BurnUploadFunding):
            updated = await conn.execute(
                """
                UPDATE evaluation_payments
                SET agent_id = $3
                WHERE payment_block_hash = $1
                  AND payment_extrinsic_index = $2
                  AND agent_id IS NULL
                """,
                funding.payment_block_hash,
                funding.payment_extrinsic_index,
                agent_id,
            )
            if updated != "UPDATE 1":
                raise UploadFundingConflictError()

        elif isinstance(funding, CreditUploadFunding):
            await conn.execute(
                """
                INSERT INTO evaluation_payments (
                    payment_block_hash,
                    payment_extrinsic_index,
                    agent_id,
                    miner_hotkey,
                    miner_coldkey,
                    amount_alpha_rao,
                    upload_credit_id
                ) VALUES ($1, '0', $2, $3, $4, 0, $5)
                """,
                agent.payment_block_hash,
                agent_id,
                funding.miner_hotkey,
                funding.miner_coldkey,
                funding.credit_id,
            )
            await conn.execute(
                """
                UPDATE upload_credits
                SET redeemed_at = clock_timestamp(), redeemed_agent_id = $2
                WHERE credit_id = $1
                """,
                funding.credit_id,
                agent_id,
            )

    return AgentAdmissionResult(agent_id=agent_id, miner_coldkey=miner_coldkey)


@db_operation
async def get_openrouter_secrets_for_agent_id(
    conn: DatabaseConnection, agent_id: UUID
) -> AgentOpenRouterSecrets | None:
    row = await conn.fetchrow(
        """
        SELECT
            runtime_api_key_ciphertext,
            management_api_key_ciphertext,
            workspace_id,
            api_key_label,
            api_key_creator_user_id,
            validated_at
        FROM agent_openrouter_secrets
        WHERE agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if row is None:
        return None

    return AgentOpenRouterSecrets(
        runtime_api_key=decrypt_agent_secret(bytes(row["runtime_api_key_ciphertext"])),
        management_api_key=decrypt_agent_secret(bytes(row["management_api_key_ciphertext"])),
        workspace_id=row["workspace_id"],
        api_key_label=row["api_key_label"],
        api_key_creator_user_id=row["api_key_creator_user_id"],
        validated_at=row["validated_at"],
    )


@db_operation
async def get_openrouter_api_key_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> str | None:
    secrets = await get_openrouter_secrets_for_agent_id(agent_id)
    return None if secrets is None else secrets.runtime_api_key


@db_operation
async def find_duplicate_source_agent_in_current_set(conn: DatabaseConnection, agent_id: UUID) -> Optional[UUID]:
    """Return the earliest other member of the same competition with this source hash."""
    return await conn.fetchval(
        """
        WITH self AS (
            SELECT agent_id, source_sha256, set_id
            FROM agents
            WHERE agent_id = $1
        )
        SELECT a.agent_id
        FROM agents a, self s
        WHERE a.agent_id <> s.agent_id
          AND s.source_sha256 IS NOT NULL
          AND a.source_sha256 = s.source_sha256
          AND a.set_id = s.set_id
        ORDER BY a.created_at ASC, a.agent_id ASC
        LIMIT 1
        """,
        agent_id,
    )


@db_operation
async def update_agent_status(conn: DatabaseConnection, agent_id: UUID, status: AgentStatus) -> None:
    await conn.execute(
        """
        UPDATE agents
        SET status = $2
        WHERE agent_id = $1
        """,
        agent_id,
        status.value,
    )


# TODO ADAM: fix this section


@db_operation
async def record_upload_attempt(conn: DatabaseConnection, upload_type: str, success: bool, **kwargs) -> None:
    # TODO ADAM: gross

    """Record an upload attempt in the upload_attempts table."""
    try:
        await conn.execute(
            """INSERT INTO upload_attempts (upload_type, success, hotkey, agent_name, filename,
                                            file_size_bytes, ip_address, error_type, error_message, ban_reason, http_status_code, agent_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            upload_type,
            success,
            kwargs.get("hotkey"),
            kwargs.get("agent_name"),
            kwargs.get("filename"),
            kwargs.get("file_size_bytes"),
            kwargs.get("ip_address"),
            kwargs.get("error_type"),
            kwargs.get("error_message"),
            kwargs.get("ban_reason"),
            kwargs.get("http_status_code"),
            kwargs.get("agent_id"),
        )
        logger.debug(
            f"Recorded upload attempt: type={upload_type}, success={success}, error_type={kwargs.get('error_type')}"
        )
    except Exception as e:
        logger.error(f"Failed to record upload attempt: {e}")


@db_operation
async def get_top_agents(
    conn: DatabaseConnection,
    set_id: int,
    number_of_agents: int = 10,
    page: int = 1,
) -> list[PublicAgent]:
    """Retrieve the top agents.

    Agents are ordered by validator score, then average validator-evaluation cost, then creation time.

    You can specify the number of results to return and the page number (for pagination).

    Parameters
    ----------
    conn : DatabaseConnection
        Database connection to use for the query
    number_of_agents : int, optional
        Number of agents to return, by default 10
    page : int, optional
        Page number for pagination, by default 1

    Returns
    -------
    list[PublicAgent]
        List of top agents with their scores.
    """
    # TODO ADAM: this query was supposed to be fixed to remove the pagination concept
    # TODO ADAM: maybe edge case bugs here if pagenum is 0,negative,or too high etc
    offset = (page - 1) * number_of_agents

    results = await conn.fetch(
        """
        select
            ass.agent_id,
            ass.miner_hotkey,
            a.miner_coldkey,
            ass.name,
            ass.version_num,
            ass.status,
            ass.created_at,
            (a.set_id IS NULL) as legacy_membership,
            ass.set_id,
            (approval.agent_id is not null) as approved,
            ass.validator_count,
            ass.final_score,
            review.approval_review_status,
            approval.performance_delta,
            approval.cost_delta,
            approval.relative_improvement_units,
            approval.time_multiplier,
            approval.initial_reward_score,
            approval.approved_at,
            baseline.agent_id as baseline_agent_id,
            baseline.name as baseline_agent_name,
            baseline.version_num as baseline_agent_version_num
        from agent_scores ass
        join agents a on a.agent_id = ass.agent_id
        left join agent_final_review_statuses review
            on review.agent_id = ass.agent_id
           and review.set_id = ass.set_id
        left join approved_agents approval
            on approval.agent_id = ass.agent_id
           and approval.set_id = ass.set_id
        left join agents baseline
            on baseline.agent_id = approval.baseline_agent_id
           and (baseline.set_id is null or baseline.set_id = ass.set_id)
        left join lateral (
            select avg(eh.avg_cost_usd) as avg_cost_usd
            from evaluations_hydrated eh
            where eh.agent_id             = ass.agent_id
              and eh.set_id               = ass.set_id
              and eh.evaluation_set_group = 'validator'::EvaluationSetGroup
              and eh.status               = 'success'::EvaluationStatus
        ) rt on true
        where ass.set_id = $3
        and (a.set_id is null or a.set_id = ass.set_id)
        and ass.agent_id not in (select agent_id from benchmark_agent_ids)
        and not exists (
            select 1
            from banned_coldkeys bc
            where bc.miner_coldkey = a.miner_coldkey
        )
        and ass.status::text <> 'cancelled'
        and review.approval_review_status is distinct from 'rejected'
        order by
            round(ass.final_score::numeric, 6) desc,
            rt.avg_cost_usd asc nulls last,
            ass.created_at asc
        limit $1 offset $2
        """,
        number_of_agents,
        offset,
        set_id,
    )

    return [PublicAgent(**agent) for agent in results]


@db_operation
async def get_code_hiding_score_cutoff(
    conn: DatabaseConnection, top_agent_count: int, top_score_count: int, set_id: int
) -> Optional[float]:
    """
    Return the lowest rounded final score whose code is hidden, or None if no agents qualify.
    """
    return await conn.fetchval(
        """
        WITH qualified AS (
            SELECT
                ROUND(ass.final_score::numeric, 6) AS score,
                ROW_NUMBER() OVER (ORDER BY ROUND(ass.final_score::numeric, 6) DESC) AS agent_rank,
                DENSE_RANK() OVER (ORDER BY ROUND(ass.final_score::numeric, 6) DESC) AS score_rank
            FROM agent_scores ass
            JOIN agents a ON a.agent_id = ass.agent_id
            LEFT JOIN agent_final_review_statuses review
                ON review.agent_id = ass.agent_id
               AND review.set_id = ass.set_id
            WHERE ass.set_id = $3
              AND (a.set_id IS NULL OR a.set_id = ass.set_id)
              AND ass.status = 'finished'
              AND ass.final_score IS NOT NULL
              AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
              AND NOT EXISTS (
                  SELECT 1
                  FROM banned_coldkeys bc
                  WHERE bc.miner_coldkey = a.miner_coldkey
              )
              AND review.approval_review_status IS DISTINCT FROM 'rejected'
        )
        SELECT LEAST(
            (SELECT MIN(score) FROM qualified WHERE agent_rank <= $1),
            (SELECT MIN(score) FROM qualified WHERE score_rank <= $2)
        )::float
        """,
        top_agent_count,
        top_score_count,
        set_id,
    )


@db_operation
async def get_agent_score_and_set_id(
    conn: DatabaseConnection,
    agent_id: UUID,
) -> Optional[tuple[int, float, int | None]]:
    """Atomically return score set, score, and raw agent membership for code hiding."""
    row = await conn.fetchrow(
        """
        SELECT
            score.set_id,
            ROUND(score.final_score::numeric, 6)::float AS final_score,
            agent.set_id AS membership_set_id
        FROM agent_scores score
        JOIN agents agent ON agent.agent_id = score.agent_id
        WHERE score.agent_id = $1
          AND score.final_score IS NOT NULL
          AND score.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        """,
        agent_id,
    )
    if row is None:
        return None
    return row["set_id"], row["final_score"], row["membership_set_id"]


@db_operation
async def get_agents_in_queue(conn: DatabaseConnection, queue_stage: QueueStage, set_id: int) -> list[Agent]:
    # TODO ALEX from ADAM: Modify this in the view itself rather than branching explicitly here.
    # The view apparently does not sort by created_at.
    queue_to_query = f"{queue_stage.value}_queue"

    if queue_stage in (QueueStage.pre_screening, QueueStage.screener_1):
        queue = await conn.fetch(
            f"""
            SELECT a.*
            from agents a
            join {queue_to_query} q on q.agent_id = a.agent_id
            where a.set_id = $1
            order by a.created_at asc
        """,
            set_id,
        )

        return [Agent(**agent) for agent in queue]

    queue = await conn.fetch(
        f"""
        SELECT a.*
        from agents a
        join {queue_to_query} q on q.agent_id = a.agent_id
        where a.set_id = $1
    """,
        set_id,
    )

    return [Agent(**agent) for agent in queue]


def _evaluation_candidate_batch(rows, *, family: EvaluationSetGroup) -> EvaluationCandidateBatch:
    if not rows:
        raise RuntimeError(f"Missing competition work cursor for {family.value}")

    return EvaluationCandidateBatch(
        observed_last_served_set_id=rows[0]["observed_last_served_set_id"],
        candidates=tuple(
            EvaluationCandidate(agent_id=row["agent_id"], set_id=row["set_id"])
            for row in rows
            if row["agent_id"] is not None
        ),
    )


@db_operation
async def get_evaluation_candidates_for_validator_hotkey(
    conn: DatabaseConnection, validator_hotkey: str
) -> EvaluationCandidateBatch:
    if validator_hotkey.startswith(("screener-1", "screener-2")):
        set_group = (
            EvaluationSetGroup.screener_1
            if validator_hotkey.startswith("screener-1")
            else EvaluationSetGroup.screener_2
        )
        expected_status = (
            AgentStatus.screening_1 if set_group is EvaluationSetGroup.screener_1 else AgentStatus.screening_2
        )
        rows = await conn.fetch(
            f"""
            WITH cursor AS MATERIALIZED (
                SELECT last_served_set_id
                FROM competition_work_cursors
                WHERE family = $1
            ),
            ranked_candidates AS MATERIALIZED (
                SELECT
                    agent.agent_id,
                    agent.set_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY agent.set_id
                        ORDER BY agent.created_at ASC, agent.agent_id ASC
                    ) AS position_in_competition
                FROM agents agent
                INNER JOIN competitions competition ON competition.set_id = agent.set_id
                WHERE agent.status = '{expected_status.value}'
                  AND competition.start_date IS NOT NULL
                  AND competition.end_date IS NULL
                  AND competition.is_paused IS FALSE
                  AND competition.scoring_mode IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM evaluations evaluation
                      WHERE evaluation.agent_id = agent.agent_id
                        AND evaluation.set_id = agent.set_id
                        AND evaluation.evaluation_set_group = '{set_group.value}'::evaluationsetgroup
                        AND (
                            SELECT CASE
                                WHEN COUNT(*) = 0 THEN NULL
                                WHEN EVERY(
                                    evaluation_run.status = 'finished'
                                    OR (
                                        evaluation_run.status = 'error'
                                        AND evaluation_run.error_code BETWEEN 1000 AND 1999
                                    )
                                ) THEN 'success'
                                WHEN EVERY(evaluation_run.status IN ('finished', 'error')) THEN 'failure'
                                ELSE 'running'
                            END
                            FROM evaluation_runs_hydrated evaluation_run
                            WHERE evaluation_run.evaluation_id = evaluation.evaluation_id
                        ) IN ('success', 'running')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM benchmark_agent_ids benchmark WHERE benchmark.agent_id = agent.agent_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM banned_coldkeys banned
                      WHERE banned.miner_coldkey = agent.miner_coldkey
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM unapproved_agent_ids unapproved WHERE unapproved.agent_id = agent.agent_id
                  )
            ),
            competition_heads AS (
                SELECT agent_id, set_id
                FROM ranked_candidates
                WHERE position_in_competition = 1
            )
            SELECT
                cursor.last_served_set_id AS observed_last_served_set_id,
                head.agent_id,
                head.set_id
            FROM cursor
            LEFT JOIN competition_heads head ON TRUE
            ORDER BY
                CASE
                    WHEN head.set_id IS NULL THEN 2
                    WHEN cursor.last_served_set_id IS NULL
                        OR head.set_id > cursor.last_served_set_id THEN 0
                    ELSE 1
                END,
                head.set_id ASC
            """,
            set_group.value,
        )
    else:
        set_group = EvaluationSetGroup.validator
        # The query is structured to force a candidates-first execution order, avoiding a
        # full scan of evaluation_runs that the planner would otherwise choose.
        #
        # Root cause of the bad plan: evaluations_hydrated is a view that groups by
        # evaluation_id. PostgreSQL cannot push a predicate past a GROUP BY from outside
        # the view, so any WHERE agent_id IN (...) filter applied to the view is evaluated
        # *after* the full aggregation — meaning all evaluation_runs are scanned and the
        # JSONB solved computation runs on every row before the candidate filter is applied.
        #
        # Here we bypass the view entirely and inline its logic using an explicit join chain:
        #   candidates (MATERIALIZED, ~1–50 rows)
        #     → evaluations by agent_id (index seek, ~10–50 rows per candidate)
        #       → evaluation_runs via JOIN LATERAL by evaluation_id (index seek, ~20–50 rows each)
        rows = await conn.fetch(
            f"""
            WITH cursor AS MATERIALIZED (
                SELECT last_served_set_id
                FROM competition_work_cursors
                WHERE family = $1
            ),
            candidates AS MATERIALIZED (
                SELECT
                    agents.agent_id,
                    agents.created_at,
                    agents.set_id,
                    competitions.required_validator_count
                FROM
                    agents
                    INNER JOIN competitions ON competitions.set_id = agents.set_id
                WHERE
                    agents.status = '{AgentStatus.evaluating.value}'
                    AND competitions.start_date IS NOT NULL
                    AND competitions.end_date IS NULL
                    AND competitions.is_paused IS FALSE
                    AND competitions.scoring_mode IS NOT NULL
                    AND competitions.required_validator_count IS NOT NULL
                    AND NOT EXISTS (
                        SELECT
                            1
                        FROM
                            benchmark_agent_ids b
                        WHERE
                            b.agent_id = agents.agent_id
                    )
            ),
            combined_eval_stats AS (
                SELECT
                    c.agent_id,
                    BOOL_OR(
                        e.validator_hotkey = $2
                        AND e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                    ) AS already_evaluated,
                    COUNT(*) FILTER (
                        WHERE
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.running.value}' :: EvaluationStatus
                    ) AS num_running_evals,
                    COUNT(*) FILTER (
                        WHERE
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                    ) AS num_finished_evals,
                    COALESCE(
                        MAX(agg.score) FILTER (
                            WHERE
                                e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                                AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                        ),
                        0
                    ) AS screener_2_score
                FROM
                    candidates c
                    JOIN evaluations e ON e.agent_id = c.agent_id
                    AND e.set_id = c.set_id
                    AND e.evaluation_set_group IN (
                        '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup,
                        '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                    )
                    JOIN LATERAL (
                        SELECT
                            (
                                CASE
                                    WHEN EVERY(
                                        er.status = 'finished'
                                        OR (
                                            er.status = 'error'
                                            AND er.error_code BETWEEN 1000
                                            AND 1999
                                        )
                                    ) THEN 'success' :: EvaluationStatus
                                    WHEN EVERY(er.status IN ('finished', 'error')) THEN 'failure' :: EvaluationStatus
                                    ELSE 'running' :: EvaluationStatus
                                END
                            ) AS computed_status,
                            COUNT(*) FILTER (WHERE er.solved) :: float / NULLIF(COUNT(*), 0) AS score
                        FROM
                            evaluation_runs_hydrated er
                        WHERE
                            er.evaluation_id = e.evaluation_id
                        HAVING COUNT(*) > 0
                    ) agg ON (
                        (
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status IN (
                                '{EvaluationStatus.success.value}' :: EvaluationStatus,
                                '{EvaluationStatus.running.value}' :: EvaluationStatus
                            )
                        )
                        OR (
                            e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                        )
                    )
                GROUP BY
                    c.agent_id
            ),
            ranked_candidates AS MATERIALIZED (
                SELECT
                    c.agent_id,
                    c.set_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.set_id
                        ORDER BY
                            COALESCE(s.screener_2_score, 0) DESC,
                            c.created_at ASC,
                            c.agent_id ASC
                    ) AS position_in_competition
                FROM candidates c
                LEFT JOIN combined_eval_stats s ON s.agent_id = c.agent_id
                WHERE NOT COALESCE(s.already_evaluated, false)
                  AND COALESCE(s.num_running_evals, 0) + COALESCE(s.num_finished_evals, 0)
                      < c.required_validator_count
            ),
            competition_heads AS (
                SELECT agent_id, set_id
                FROM ranked_candidates
                WHERE position_in_competition = 1
            )
            SELECT
                cursor.last_served_set_id AS observed_last_served_set_id,
                head.agent_id,
                head.set_id
            FROM cursor
            LEFT JOIN competition_heads head ON TRUE
            ORDER BY
                CASE
                    WHEN head.set_id IS NULL THEN 2
                    WHEN cursor.last_served_set_id IS NULL
                        OR head.set_id > cursor.last_served_set_id THEN 0
                    ELSE 1
                END,
                head.set_id ASC
            """,
            set_group.value,
            validator_hotkey,
        )

    return _evaluation_candidate_batch(rows, family=set_group)


@db_operation
async def get_pending_work_counts(conn: DatabaseConnection) -> dict[str, int]:
    row = await conn.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM screener_1_queue) AS screener_1_pending,
            (SELECT COUNT(*) FROM screener_2_queue) AS screener_2_pending
    """)
    return {
        "screener_1_pending": row["screener_1_pending"],
        "screener_2_pending": row["screener_2_pending"],
    }


@db_operation
async def get_all_public_agents_by_miner_coldkey(conn: DatabaseConnection, miner_coldkey: str) -> list[PublicAgent]:
    """All agents stamped with this coldkey at upload time.

    Rows with a NULL coldkey are excluded — miner_coldkey was added 2026-07-10
    without backfill, so agents uploaded before then (and dev uploads) won't appear.
    """
    result = await conn.fetch(
        f"""
        SELECT
            {AGENT_PUBLIC_SELECT_COLUMNS}
        FROM agents a
        {AGENT_PUBLIC_JOINS}
        WHERE a.miner_coldkey = $1
        ORDER BY a.miner_hotkey, a.created_at DESC, a.agent_id
        """,
        miner_coldkey,
    )
    return [PublicAgent(**agent) for agent in result]
