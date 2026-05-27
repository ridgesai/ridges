"""Extend pre_screening_jobs for projection, review, and announcements

Revision ID: f2a8b1d7e904
Revises: c4d2e7a9f158
Create Date: 2026-05-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f2a8b1d7e904"
down_revision: Union[str, Sequence[str], None] = "c4d2e7a9f158"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pre_screening_jobs",
        sa.Column("projected_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("pre_screening_jobs", sa.Column("discord_channel_id", sa.Text(), nullable=True))
    op.add_column("pre_screening_jobs", sa.Column("discord_message_id", sa.Text(), nullable=True))
    op.add_column("pre_screening_jobs", sa.Column("discord_thread_id", sa.Text(), nullable=True))
    op.add_column("pre_screening_jobs", sa.Column("reviewer_id", sa.Text(), nullable=True))
    op.add_column(
        "pre_screening_jobs",
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "pre_screening_jobs",
        sa.Column("review_requested_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "pre_screening_jobs",
        sa.Column("announcement_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_pre_screening_jobs_discord_message_pair",
        "pre_screening_jobs",
        "(discord_channel_id IS NULL) = (discord_message_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_pre_screening_jobs_discord_thread_requires_message",
        "pre_screening_jobs",
        "discord_thread_id IS NULL OR (discord_channel_id IS NOT NULL AND discord_message_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_pre_screening_jobs_reviewer_pair",
        "pre_screening_jobs",
        "(reviewer_id IS NULL) = (reviewed_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_pre_screening_jobs_reviewer_requires_terminal_status",
        "pre_screening_jobs",
        "reviewed_at IS NULL OR status IN ('succeeded', 'failed')",
    )

    op.create_index(
        "idx_pre_screening_jobs_unprojected_terminal",
        "pre_screening_jobs",
        ["created_at"],
        postgresql_where=sa.text("status IN ('succeeded', 'failed', 'needs_review') AND projected_at IS NULL"),
    )
    op.create_index(
        "idx_pre_screening_jobs_needs_review_discord_unposted",
        "pre_screening_jobs",
        ["review_requested_at", "created_at"],
        postgresql_where=sa.text("status = 'needs_review' AND discord_message_id IS NULL"),
    )
    op.create_index(
        "idx_pre_screening_jobs_pending_announcement",
        "pre_screening_jobs",
        ["created_at"],
        postgresql_where=sa.text("status = 'failed' AND announcement_sent_at IS NULL AND reviewer_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pre_screening_jobs_pending_announcement",
        table_name="pre_screening_jobs",
    )
    op.drop_index(
        "idx_pre_screening_jobs_needs_review_discord_unposted",
        table_name="pre_screening_jobs",
    )
    op.drop_index("idx_pre_screening_jobs_unprojected_terminal", table_name="pre_screening_jobs")
    op.drop_constraint(
        "ck_pre_screening_jobs_reviewer_requires_terminal_status",
        "pre_screening_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_pre_screening_jobs_reviewer_pair",
        "pre_screening_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_pre_screening_jobs_discord_thread_requires_message",
        "pre_screening_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_pre_screening_jobs_discord_message_pair",
        "pre_screening_jobs",
        type_="check",
    )
    op.drop_column("pre_screening_jobs", "announcement_sent_at")
    op.drop_column("pre_screening_jobs", "review_requested_at")
    op.drop_column("pre_screening_jobs", "reviewed_at")
    op.drop_column("pre_screening_jobs", "reviewer_id")
    op.drop_column("pre_screening_jobs", "discord_thread_id")
    op.drop_column("pre_screening_jobs", "discord_message_id")
    op.drop_column("pre_screening_jobs", "discord_channel_id")
    op.drop_column("pre_screening_jobs", "projected_at")
