from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class InternalFlagName(str, Enum):
    """Defines the known Internal Flags in the system. Each flag's value is stored as a string in the DB, but the application can interpret it according to the flag's expected type (e.g. boolean, list of strings)."""

    # Pause/Unpause all validators and screeners
    VALIDATORS_PAUSED = "VALIDATORS_PAUSED"
    # List of blacklisted hotkeys (validators or screeners, stored as JSON array)
    BLACKLISTED_VALIDATORS = "BLACKLISTED_VALIDATORS"


class InternalFlag(Base, CreatedAtMixin):
    """Internal Flag model.

    This is a flexible way of storing configurations and
    modifying the behavior of the system without needing
    to deploy code changes.
    """

    __tablename__ = "internal_flags"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )
    name: Mapped[InternalFlagName] = mapped_column(
        sa.Enum(InternalFlagName, name="internalflagname"),
        unique=True,
        comment="The name of the flag, e.g. 'VALIDATORS_PAUSED'",
    )
    value: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The value of the flag, stored as a string. Interpretation depends on the flag.",
    )
    description: Mapped[Optional[str]] = mapped_column(sa.Text, comment="A description of what the flag does")
    updated_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
