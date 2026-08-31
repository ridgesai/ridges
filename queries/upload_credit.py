from datetime import datetime
from typing import Optional
from uuid import UUID

from models.agent import AgentCreate
from models.upload_credit import UploadCredit
from queries.agent import CreditUploadFunding, _derive_agent_id, admit_agent
from queries.competition import resolve_upload_competition
from queries.errors import UploadCreditAlreadyRedeemedError
from utils.database import DatabaseConnection, db_operation
from utils.s3 import upload_text_file_to_s3


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
async def get_upload_credit_by_id(
    conn: DatabaseConnection,
    credit_id: UUID,
    miner_hotkey: str,
) -> Optional[UploadCredit]:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM upload_credits
        WHERE credit_id = $1
          AND miner_hotkey = $2
        """,
        credit_id,
        miner_hotkey,
    )
    return UploadCredit(**row) if row is not None else None


@db_operation
async def get_exact_upload_credit_replay(
    conn: DatabaseConnection,
    *,
    credit_id: UUID,
    miner_hotkey: str,
    source_sha256: str,
    set_id: int | None,
) -> UUID | None:
    """Return an exact successful credit replay without consulting lifecycle state."""
    row = await conn.fetchrow(
        """
        SELECT
            credit.redeemed_agent_id,
            agent.miner_hotkey AS agent_hotkey,
            agent.source_sha256,
            agent.set_id
        FROM upload_credits credit
        LEFT JOIN agents agent ON agent.agent_id = credit.redeemed_agent_id
        WHERE credit.credit_id = $1
          AND credit.miner_hotkey = $2
        """,
        credit_id,
        miner_hotkey,
    )
    if row is None or row["redeemed_agent_id"] is None:
        return None

    if (
        row["agent_hotkey"] == miner_hotkey
        and row["source_sha256"] == source_sha256
        and (set_id is None or row["set_id"] == set_id)
    ):
        return row["redeemed_agent_id"]
    raise UploadCreditAlreadyRedeemedError(row["redeemed_agent_id"])


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
    set_id: int,
) -> tuple[UUID, bool]:
    """Compatibility wrapper for credit admission used by focused query tests."""
    payment_block_hash, payment_extrinsic_index = credit_payment_identity(credit_id)
    credit_agent = agent.model_copy(
        update={
            "payment_block_hash": payment_block_hash,
            "payment_extrinsic_index": payment_extrinsic_index,
        }
    )

    resolved_set_id = await resolve_upload_competition(set_id)
    agent_id = _derive_agent_id(payment_block_hash, payment_extrinsic_index)
    await upload_text_file_to_s3(f"{agent_id}/agent.py", agent_text)
    result = await admit_agent(
        credit_agent,
        set_id=resolved_set_id,
        source_sha256=source_sha256,
        runtime_openrouter_api_key_ciphertext=runtime_openrouter_api_key_ciphertext,
        management_openrouter_api_key_ciphertext=management_openrouter_api_key_ciphertext,
        openrouter_workspace_id=openrouter_workspace_id,
        openrouter_api_key_label=openrouter_api_key_label,
        openrouter_api_key_creator_user_id=openrouter_api_key_creator_user_id,
        openrouter_validated_at=openrouter_validated_at,
        miner_coldkey=miner_coldkey,
        funding=CreditUploadFunding(
            credit_id=credit_id,
            miner_hotkey=miner_hotkey,
            miner_coldkey=miner_coldkey,
        ),
        enforce_cooldown=False,
    )
    return result.agent_id, result.replayed
