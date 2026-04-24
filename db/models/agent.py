from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models.enums import AgentStatus


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_num: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[Optional[AgentStatus]] = mapped_column(
        sa.Enum(AgentStatus, name="agentstatus")
    )
    # No server_default — the application always supplies this explicitly via NOW()
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False
    )
    ip_address: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.Index(
            "idx_agents_miner_hotkey_version", "miner_hotkey", "agent_id"
        ),
        sa.Index(
            "idx_agents_status",
            "status",
            postgresql_where=sa.text("status = 'evaluating'"),
        ),
    )


class BannedHotkey(Base):
    __tablename__ = "banned_hotkeys"

    # TODO: There is no PK in the original schema, should we consider the minor hotkey as the PK here ?
    miner_hotkey: Mapped[str] = mapped_column(
        sa.Text, nullable=False, primary_key=True
    )
    banned_reason: Mapped[Optional[str]] = mapped_column(sa.Text)
    banned_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )

    __table_args__ = (
        sa.Index("idx_banned_hotkeys_miner_hotkey", "miner_hotkey"),
    )


class BenchmarkAgentId(Base):
    __tablename__ = "benchmark_agent_ids"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("agents.agent_id"),
        primary_key=True,
    )
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)


class UnapprovedAgentId(Base):
    __tablename__ = "unapproved_agent_ids"

    # TODO: The original schema defined it as a unique index, should we consider the agent_id as the PK here ?
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("agents.agent_id"),
        primary_key=True,
    )
    unapproved_reason: Mapped[Optional[str]] = mapped_column(sa.Text)
    unapproved_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )


class AgentScore(Base):
    __tablename__ = "agent_scores"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_num: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False
    )
    status: Mapped[AgentStatus] = mapped_column(
        sa.Enum(AgentStatus, name="agentstatus"), nullable=False
    )
    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    approved: Mapped[Optional[bool]] = mapped_column(sa.Boolean)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        sa.TIMESTAMP(timezone=True)
    )
    validator_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    final_score: Mapped[float] = mapped_column(sa.Float, nullable=False)

    __table_args__ = (
        sa.Index("idx_agent_scores_final_score", "final_score"),
        sa.Index("idx_agent_scores_created_at", "created_at"),
    )
