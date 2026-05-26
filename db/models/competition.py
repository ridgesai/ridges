from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class Competition(Base, CreatedAtMixin):
    __tablename__ = "competitions"

    set_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(sa.Text)
    start_date: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
