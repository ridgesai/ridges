from datetime import datetime
from typing import Optional
from uuid import UUID

from models.agent import AgentCreate
from models.upload_credit import UploadCredit
from queries.agent import create_agent
from queries.errors import UploadCreditAlreadyRedeemedError, UploadCreditUnavailableError
from utils.database import DatabaseConnection, db_operation


def credit_payment_identity(credit_id: UUID) -> tuple[str, str]:
    """Return the canonical synthetic payment identity for a credit."""
    return f"credit:{credit_id}", "0"


@db_operation
async def grant_upload_credit(
    conn: DatabaseConnection,
    *,
    miner_hotkey: str,
    reason: str,
    granted_by: str,
    grant_reference: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> Optional[UploadCredit]:
    """Grant one credit, returning an existing credit when its reference is retried."""
    row = await conn.fetchrow(
        """
        INSERT INTO upload_credits (miner_hotkey, reason, granted_by, grant_reference, expires_at)
        SELECT $1, $2, $3, $4, $5::timestamptz
        WHERE $5::timestamptz IS NULL OR $5 > NOW()
        ON CONFLICT (miner_hotkey, grant_reference) DO UPDATE
        SET grant_reference = EXCLUDED.grant_reference
        RETURNING *
        """,
        miner_hotkey,
        reason,
        granted_by,
        grant_reference,
        expires_at,
    )
    return UploadCredit(**row) if row is not None else None


@db_operation
async def get_upload_credit_for_check(
    conn: DatabaseConnection,
    miner_hotkey: str,
    credit_id: Optional[UUID] = None,
) -> Optional[UploadCredit]:
    """Return a usable credit, or a specifically requested credit for an idempotent retry."""
    if credit_id is None:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM upload_credits
            WHERE miner_hotkey = $1
              AND redeemed_at IS NULL
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY granted_at, credit_id
            LIMIT 1
            """,
            miner_hotkey,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM upload_credits
            WHERE credit_id = $1
              AND miner_hotkey = $2
              AND revoked_at IS NULL
              AND (redeemed_at IS NOT NULL OR expires_at IS NULL OR expires_at > NOW())
            """,
            credit_id,
            miner_hotkey,
        )

    return UploadCredit(**row) if row is not None else None


@db_operation
async def create_agent_with_upload_credit(
    conn: DatabaseConnection,
    *,
    credit_id: UUID,
    miner_hotkey: str,
    miner_coldkey: str,
    agent: AgentCreate,
    agent_text: str,
    source_sha256: str,
    runtime_openrouter_api_key_ciphertext: bytes,
    management_openrouter_api_key_ciphertext: bytes,
    openrouter_workspace_id: str,
    openrouter_api_key_label: str,
    openrouter_api_key_creator_user_id: str,
    openrouter_validated_at: datetime,
    create_pre_screening_job: bool = False,
) -> tuple[UUID, bool]:
    """Create an agent and consume exactly one credit in a single database transaction."""
    payment_block_hash, payment_extrinsic_index = credit_payment_identity(credit_id)
    credit_agent = agent.model_copy(
        update={
            "payment_block_hash": payment_block_hash,
            "payment_extrinsic_index": payment_extrinsic_index,
        }
    )

    async with conn.conn.transaction():
        credit = await conn.fetchrow(
            """
            SELECT *, expires_at IS NOT NULL AND expires_at <= NOW() AS is_expired
            FROM upload_credits
            WHERE credit_id = $1
              AND miner_hotkey = $2
            FOR UPDATE
            """,
            credit_id,
            miner_hotkey,
        )
        if credit is None or credit["revoked_at"] is not None:
            raise UploadCreditUnavailableError()

        if credit["redeemed_at"] is None and credit["is_expired"]:
            raise UploadCreditUnavailableError()

        if credit["redeemed_agent_id"] is not None:
            existing = await conn.fetchrow(
                """
                SELECT miner_hotkey, source_sha256
                FROM agents
                WHERE agent_id = $1
                """,
                credit["redeemed_agent_id"],
            )
            if (
                existing is not None
                and existing["miner_hotkey"] == miner_hotkey
                and existing["source_sha256"] == source_sha256
            ):
                return credit["redeemed_agent_id"], True
            raise UploadCreditAlreadyRedeemedError(credit["redeemed_agent_id"])

        agent_id = await create_agent(
            credit_agent,
            agent_text,
            source_sha256=source_sha256,
            runtime_openrouter_api_key_ciphertext=runtime_openrouter_api_key_ciphertext,
            management_openrouter_api_key_ciphertext=management_openrouter_api_key_ciphertext,
            openrouter_workspace_id=openrouter_workspace_id,
            openrouter_api_key_label=openrouter_api_key_label,
            openrouter_api_key_creator_user_id=openrouter_api_key_creator_user_id,
            openrouter_validated_at=openrouter_validated_at,
            miner_coldkey=miner_coldkey,
            create_pre_screening_job=create_pre_screening_job,
        )

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
            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            payment_block_hash,
            payment_extrinsic_index,
            agent_id,
            miner_hotkey,
            miner_coldkey,
            credit_id,
        )
        await conn.execute(
            """
            UPDATE upload_credits
            SET redeemed_at = NOW(), redeemed_agent_id = $2
            WHERE credit_id = $1
            """,
            credit_id,
            agent_id,
        )

    return agent_id, False
