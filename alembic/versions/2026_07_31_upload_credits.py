from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "550060c2f8a7"
down_revision: Union[str, Sequence[str], None] = "b3f1a9c4d210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_credits",
        sa.Column(
            "credit_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.Text(), nullable=False),
        sa.Column("grant_reference", sa.Text(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redeemed_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "idx_upload_credits_available_hotkey",
        "upload_credits",
        ["miner_hotkey", "granted_at"],
        postgresql_where=sa.text("redeemed_at IS NULL AND revoked_at IS NULL"),
    )

    op.add_column(
        "evaluation_payments",
        sa.Column("upload_credit_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_evaluation_payments_upload_credit_id",
        "evaluation_payments",
        "upload_credits",
        ["upload_credit_id"],
        ["credit_id"],
    )
    op.create_unique_constraint(
        "uq_evaluation_payments_upload_credit_id",
        "evaluation_payments",
        ["upload_credit_id"],
    )
    op.create_check_constraint(
        "ck_evaluation_payments_credit_shape",
        "evaluation_payments",
        "upload_credit_id IS NULL OR (amount_alpha_rao = 0 AND amount_rao IS NULL AND quote_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evaluation_payments_credit_shape", "evaluation_payments", type_="check")
    op.drop_constraint("uq_evaluation_payments_upload_credit_id", "evaluation_payments", type_="unique")
    op.drop_constraint("fk_evaluation_payments_upload_credit_id", "evaluation_payments", type_="foreignkey")
    op.drop_column("evaluation_payments", "upload_credit_id")
    op.drop_index("idx_upload_credits_available_hotkey", table_name="upload_credits")
    op.drop_table("upload_credits")
