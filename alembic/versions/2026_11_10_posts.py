"""posts

Revision ID: a4d9c2e17b63
Revises: e6b1f0c3d9a2
Create Date: 2026-11-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a4d9c2e17b63"
down_revision: Union[str, Sequence[str], None] = "e6b1f0c3d9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("image_s3_key", sa.Text(), nullable=True, comment="S3 object key of the post image"),
        sa.Column("image_content_type", sa.Text(), nullable=True),
        sa.Column(
            "unique_thumbnail_id",
            sa.Text(),
            nullable=True,
            comment="Frontend-provided id of a pre-built thumbnail component. May repeat across posts.",
        ),
        sa.Column("featured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "image_s3_key IS NOT NULL OR unique_thumbnail_id IS NOT NULL",
            name="ck_posts_image_or_thumbnail",
        ),
        sa.CheckConstraint(
            "(image_s3_key IS NULL) = (image_content_type IS NULL)",
            name="ck_posts_image_content_type_pair",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_posts_featured_created_at", "posts", ["featured", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_posts_featured_created_at", table_name="posts")
    op.drop_table("posts")
