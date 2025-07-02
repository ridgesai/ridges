import os
from typing import Optional, List
from sqlalchemy import create_engine, select, func, and_, or_
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime, timedelta
from dotenv import load_dotenv

from .sqlalchemy_models import Base, AgentModel, AgentVersionModel, EvaluationModel, EvaluationRunModel, WeightsHistoryModel
from api.src.utils.models import Agent, AgentVersion, Evaluation, EvaluationRun, AgentSummary, WeightsData
from api.src.utils.logging_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

class SQLAlchemyDatabaseManager:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._initialize_engine()
    
    def _initialize_engine(self):
        """Initialize the SQLAlchemy engine and session maker"""
        try:
            database_url = f"postgresql://{os.getenv('AWS_MASTER_USERNAME')}:{os.getenv('AWS_MASTER_PASSWORD')}@{os.getenv('AWS_RDS_PLATFORM_ENDPOINT')}/{os.getenv('AWS_RDS_PLATFORM_DB_NAME')}"
            
            self.engine = create_engine(
                database_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False
            )
            
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            logger.info("SQLAlchemy engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize SQLAlchemy engine: {str(e)}")
            raise
    
    def get_session(self) -> Session:
        """Get a new database session"""
        return self.SessionLocal()
    
    def store_agent(self, agent: Agent) -> int:
        """Store an agent using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            # Use PostgreSQL's ON CONFLICT DO UPDATE
            stmt = insert(AgentModel).values(
                agent_id=agent.agent_id,
                miner_hotkey=agent.miner_hotkey,
                name=agent.name,
                latest_version=agent.latest_version,
                created_at=agent.created_at,
                last_updated=agent.last_updated
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['agent_id'],
                set_=dict(
                    latest_version=stmt.excluded.latest_version,
                    last_updated=stmt.excluded.last_updated
                )
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"Agent {agent.agent_id} stored successfully via SQLAlchemy")
            return 1
        except Exception as e:
            session.rollback()
            logger.error(f"Error storing agent {agent.agent_id} via SQLAlchemy: {str(e)}")
            return 0
        finally:
            session.close()
    
    def store_agent_version(self, agent_version: AgentVersion) -> int:
        """Store an agent version using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            stmt = insert(AgentVersionModel).values(
                version_id=agent_version.version_id,
                agent_id=agent_version.agent_id,
                version_num=agent_version.version_num,
                created_at=agent_version.created_at,
                score=agent_version.score
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['version_id'],
                set_=dict(score=stmt.excluded.score)
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"Agent version {agent_version.version_id} stored successfully via SQLAlchemy")
            return 1
        except Exception as e:
            session.rollback()
            logger.error(f"Error storing agent version {agent_version.version_id} via SQLAlchemy: {str(e)}")
            return 0
        finally:
            session.close()
    
    def get_agent_by_hotkey(self, miner_hotkey: str) -> Optional[Agent]:
        """Get an agent by miner hotkey using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            agent_model = session.query(AgentModel).filter(AgentModel.miner_hotkey == miner_hotkey).first()
            if agent_model:
                return Agent(
                    agent_id=str(agent_model.agent_id),
                    miner_hotkey=agent_model.miner_hotkey,
                    name=agent_model.name,
                    latest_version=agent_model.latest_version,
                    created_at=agent_model.created_at,
                    last_updated=agent_model.last_updated
                )
            return None
        finally:
            session.close()
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            agent_model = session.query(AgentModel).filter(AgentModel.agent_id == agent_id).first()
            if agent_model:
                return Agent(
                    agent_id=str(agent_model.agent_id),
                    miner_hotkey=agent_model.miner_hotkey,
                    name=agent_model.name,
                    latest_version=agent_model.latest_version,
                    created_at=agent_model.created_at,
                    last_updated=agent_model.last_updated
                )
            return None
        finally:
            session.close()
    
    def get_agent_version(self, version_id: str) -> Optional[AgentVersion]:
        """Get an agent version using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            version_model = session.query(AgentVersionModel).filter(AgentVersionModel.version_id == version_id).first()
            if version_model:
                return AgentVersion(
                    version_id=str(version_model.version_id),
                    agent_id=str(version_model.agent_id),
                    version_num=version_model.version_num,
                    created_at=version_model.created_at,
                    score=version_model.score
                )
            return None
        finally:
            session.close()
    
    def get_latest_agent_version(self, agent_id: str) -> Optional[AgentVersion]:
        """Get the latest agent version using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            version_model = session.query(AgentVersionModel).filter(
                AgentVersionModel.agent_id == agent_id
            ).order_by(AgentVersionModel.created_at.desc()).first()
            
            if version_model:
                return AgentVersion(
                    version_id=str(version_model.version_id),
                    agent_id=str(version_model.agent_id),
                    version_num=version_model.version_num,
                    created_at=version_model.created_at,
                    score=version_model.score
                )
            return None
        finally:
            session.close()
    
    def get_num_agents(self) -> int:
        """Get the number of agents using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            return session.query(func.count(AgentModel.agent_id)).scalar()
        finally:
            session.close()
    
    def store_weights(self, miner_weights: dict, time_since_last_update=None) -> int:
        """Store weights using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            weights_record = WeightsHistoryModel(
                timestamp=datetime.now(),
                time_since_last_update=time_since_last_update,
                miner_weights=miner_weights
            )
            session.add(weights_record)
            session.commit()
            logger.info(f"Weights stored successfully via SQLAlchemy with {len(miner_weights)} miners")
            return 1
        except Exception as e:
            session.rollback()
            logger.error(f"Error storing weights via SQLAlchemy: {str(e)}")
            return 0
        finally:
            session.close()
    
    def get_latest_weights(self) -> Optional[dict]:
        """Get the latest weights using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            weights_model = session.query(WeightsHistoryModel).order_by(
                WeightsHistoryModel.timestamp.desc()
            ).first()
            
            if weights_model:
                return {
                    'weights': weights_model.miner_weights,
                    'timestamp': weights_model.timestamp,
                    'time_since_last_update': weights_model.time_since_last_update
                }
            return None
        finally:
            session.close()
    
    def get_current_top_miner(self) -> Optional[str]:
        """Get the current top miner using SQLAlchemy ORM"""
        session = self.get_session()
        try:
            weights_model = session.query(WeightsHistoryModel).order_by(
                WeightsHistoryModel.timestamp.desc()
            ).first()
            
            if not weights_model or not weights_model.miner_weights:
                return None
            
            # Find the miner with the highest weight
            top_miner_hotkey = max(weights_model.miner_weights.items(), key=lambda x: float(x[1]))[0]
            logger.info(f"Current top miner: {top_miner_hotkey}")
            return top_miner_hotkey
        finally:
            session.close()