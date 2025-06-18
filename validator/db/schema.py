from datetime import datetime

from sqlalchemy import Integer, String, DateTime, Boolean, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from validator.config import DB_PATH
from shared.logging_utils import get_logger
from validator.dependancies import get_database_engine

logger = get_logger(__name__)

class Base(DeclarativeBase):
    pass

def init_db() -> None:
    logger.info(f"Initializing database manager with path: {DB_PATH}")
    engine = get_database_engine()
    Base.metadata.create_all(engine)

class AgentVersion(Base):
    __tablename__ = "agent_versions"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    miner_hotkey: Mapped[str] = mapped_column(String, nullable=False)
    version_num: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=True)
    
class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    version_id: Mapped[str] = mapped_column(String, nullable=False)
    validator_hotkey: Mapped[str] = mapped_column(String, nullable=False)
    swebench_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    response: Mapped[str] = mapped_column(String, nullable=True)
    fail_to_pass_success: Mapped[int] = mapped_column(String, nullable=True)
    pass_to_pass_success: Mapped[int] = mapped_column(String, nullable=True)
    fail_to_fail_success: Mapped[int] = mapped_column(String, nullable=True)
    pass_to_fail_success: Mapped[int] = mapped_column(String, nullable=True)
    solved: Mapped[bool] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)