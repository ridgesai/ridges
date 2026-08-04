from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class UploadCredit(Base):
    __tablename__ = "upload_credits"

    credit_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    granted_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    grant_reference: Mapped[Optional[str]] = mapped_column(sa.Text)
    granted_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    redeemed_agent_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id"), unique=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    revoked_by: Mapped[Optional[str]] = mapped_column(sa.Text)
    revoke_reason: Mapped[Optional[str]] = mapped_column(sa.Text)

    __table_args__ = (
        sa.CheckConstraint(
            "(redeemed_at IS NULL) = (redeemed_agent_id IS NULL)",
            name="ck_upload_credits_redemption_complete",
        ),
        sa.CheckConstraint(
            "NOT (redeemed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_upload_credits_not_redeemed_and_revoked",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_upload_credits_expiry_after_grant",
        ),
        sa.UniqueConstraint("miner_hotkey", "grant_reference", name="uq_upload_credits_grant_reference"),
        sa.Index(
            "idx_upload_credits_available_hotkey",
            "miner_hotkey",
            "granted_at",
            postgresql_where=sa.text("redeemed_at IS NULL AND revoked_at IS NULL"),
        ),
    )
