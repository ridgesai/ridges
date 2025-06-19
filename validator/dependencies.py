from functools import lru_cache
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from validator.config import DB_PATH
from typing import Generator

@lru_cache()
def get_database_engine():
    """Get SQLAlchemy database engine."""
    return create_engine(
        f"sqlite:///{DB_PATH}",
        pool_size=10,
        max_overflow=20,
        pool_timeout=30
    )

@lru_cache()
def get_session_factory():
    """Get SQLAlchemy session factory."""
    engine = get_database_engine()
    return sessionmaker(bind=engine)

def get_db_session() -> Generator[Session, None, None]:
    """Get a new database session."""
    SessionFactory = get_session_factory()
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()

def get_storage_dir(dirname: str) -> Path:
    """Get path to a storage directory."""
    storage_dir = Path(__file__).parent / dirname
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir
