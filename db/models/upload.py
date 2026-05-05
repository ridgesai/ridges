from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class UploadAttempt(Base, CreatedAtMixin):
    __tablename__ = "upload_attempts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    upload_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hotkey: Mapped[Optional[str]] = mapped_column(sa.Text)
    agent_name: Mapped[Optional[str]] = mapped_column(sa.Text)
    filename: Mapped[Optional[str]] = mapped_column(sa.Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(sa.BigInteger)
    ip_address: Mapped[Optional[str]] = mapped_column(sa.Text)
    success: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    error_type: Mapped[Optional[str]] = mapped_column(sa.Text)
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text)
    ban_reason: Mapped[Optional[str]] = mapped_column(sa.Text)
    http_status_code: Mapped[Optional[int]] = mapped_column(sa.Integer)
    agent_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
