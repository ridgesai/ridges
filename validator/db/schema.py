from datetime import datetime
from typing import List
from pathlib import Path
import logging
from enum import Enum

from sqlalchemy import ForeignKey, Integer, String, DateTime, Boolean, Float, LargeBinary, create_engine, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

class AssignmentStatus(str, Enum):
    ASSIGNED = "assigned"
    SENT = "sent"
    COMPLETED = "completed"
    FAILED = "failed"

class Challenge(Base):
    __tablename__ = "challenges"

    challenge_id: Mapped[str] = mapped_column(String, primary_key=True)
    challenge_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    codegen_challenges: Mapped["CodegenChallenge"] = relationship(back_populates="challenge")
    regression_challenges: Mapped["RegressionChallenge"] = relationship(back_populates="challenge")
    assignments: Mapped[List["ChallengeAssignment"]] = relationship(back_populates="challenge")
    responses: Mapped[List["Response"]] = relationship(back_populates="challenge")

class CodegenChallenge(Base):
    __tablename__ = "codegen_challenges"

    challenge_id: Mapped[str] = mapped_column(
        String, 
        ForeignKey("challenges.challenge_id", ondelete="CASCADE"),
        primary_key=True
    )
    problem_statement: Mapped[str] = mapped_column(String, nullable=False)
    dynamic_checklist: Mapped[str] = mapped_column(String, nullable=False)
    repository_url: Mapped[str] = mapped_column(String, nullable=False)
    commit_hash: Mapped[str] = mapped_column(String, nullable=True)
    context_file_paths: Mapped[str] = mapped_column(String, nullable=False)

    challenge: Mapped["Challenge"] = relationship(back_populates="codegen_challenges")

class RegressionChallenge(Base):
    __tablename__ = "regression_challenges"

    challenge_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("challenges.challenge_id", ondelete="CASCADE"),
        primary_key=True
    )
    problem_statement: Mapped[str] = mapped_column(String, nullable=False)
    repository_url: Mapped[str] = mapped_column(String, nullable=False)
    commit_hash: Mapped[str] = mapped_column(String, nullable=True)
    context_file_paths: Mapped[str] = mapped_column(String, nullable=False)

    challenge: Mapped["Challenge"] = relationship(back_populates="regression_challenges")

class ChallengeAssignment(Base):
    __tablename__ = "challenge_assignments"

    assignment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[str] = mapped_column(String, ForeignKey("challenges.challenge_id"), nullable=False)
    miner_hotkey: Mapped[str] = mapped_column(String, nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        SQLEnum(AssignmentStatus),
        default=AssignmentStatus.ASSIGNED,
        nullable=False
    )

    challenge: Mapped["Challenge"] = relationship(back_populates="assignments")
    response: Mapped["Response"] = relationship(back_populates="assignment")

    __table_args__ = (
        UniqueConstraint('challenge_id', 'miner_hotkey', name='uix_challenge_miner'),
    )

class Response(Base):
    __tablename__ = "responses"

    response_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[str] = mapped_column(
        String, 
        ForeignKey("challenges.challenge_id"),
        nullable=False
    )
    miner_hotkey: Mapped[str] = mapped_column(String, nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, nullable=True)
    processing_time: Mapped[float] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    evaluated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    response_patch: Mapped[str] = mapped_column(String, nullable=True)

    challenge: Mapped["Challenge"] = relationship(back_populates="responses")
    assignment: Mapped["ChallengeAssignment"] = relationship(
        back_populates="response",
        foreign_keys=[challenge_id, miner_hotkey],
        primaryjoin="and_(Response.challenge_id==ChallengeAssignment.challenge_id, "
                   "Response.miner_hotkey==ChallengeAssignment.miner_hotkey)"
    )

class AvailabilityCheck(Base):
    __tablename__ = "availability_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    hotkey: Mapped[str] = mapped_column(String, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    response_time_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str] = mapped_column(String, nullable=True)

def init_db(db_path: str) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
