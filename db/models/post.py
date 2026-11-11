from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class Post(Base, CreatedAtMixin):
    """A post shown on the frontend.

    The image bytes live in S3, only the object key is stored here. A post must have an image,
    a `unique_thumbnail_id` (which the frontend maps to a pre-built component), or both.
    """

    __tablename__ = "posts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    image_s3_key: Mapped[Optional[str]] = mapped_column(sa.Text, comment="S3 object key of the post image")
    image_content_type: Mapped[Optional[str]] = mapped_column(sa.Text)
    unique_thumbnail_id: Mapped[Optional[str]] = mapped_column(
        sa.Text,
        comment="Frontend-provided id of a pre-built thumbnail component. May repeat across posts.",
    )
    featured: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "image_s3_key IS NOT NULL OR unique_thumbnail_id IS NOT NULL",
            name="ck_posts_image_or_thumbnail",
        ),
        sa.CheckConstraint(
            "(image_s3_key IS NULL) = (image_content_type IS NULL)",
            name="ck_posts_image_content_type_pair",
        ),
        sa.Index("idx_posts_featured_created_at", "featured", sa.text("created_at DESC")),
    )
