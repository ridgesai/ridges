from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CreatedAtMixin:
    """Adds created_at with a server-side NOW() default.

    Use on tables where the DB sets this automatically (i.e. the column has DEFAULT NOW()).
    Tables where the application always supplies the value explicitly should define
    created_at directly without server_default.
    """

    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )
