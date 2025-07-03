import os
from typing import Optional, List
import json
import threading
import uuid
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func, and_, or_, text, Integer
from sqlalchemy.dialects.postgresql import insert

from api.src.utils.models import AgentSummary, Execution, AgentSummaryResponse, AgentDetailsNew, AgentVersionNew, ExecutionNew, AgentVersionDetails, WeightsData, QueueInfo, TopAgentHotkey, AgentVersionResponse, AgentResponse, EvaluationResponse, EvaluationRunResponse, RunningAgentEval
from api.src.utils.logging_utils import get_logger
from .sqlalchemy_models import Base, Agent, AgentVersion, Evaluation, EvaluationRun, WeightsHistory, BannedHotkey

load_dotenv()

logger = get_logger(__name__)

class DatabaseManager:
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    AsyncSessionLocal: async_sessionmaker[AsyncSession]
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    async def init(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.engine = None
                    self.AsyncSessionLocal = None
                    self._initialize_engine()
                    await self._init_tables()
                    self._initialized = True
    
    def _initialize_engine(self):
        """Initialize the async SQLAlchemy engine and session maker"""
        try:
            # Use asyncpg as the async driver
            database_url = f"postgresql+asyncpg://{os.getenv('AWS_MASTER_USERNAME')}:{os.getenv('AWS_MASTER_PASSWORD')}@{os.getenv('AWS_RDS_PLATFORM_ENDPOINT')}/{os.getenv('AWS_RDS_PLATFORM_DB_NAME')}"
            
            self.engine = create_async_engine(
                database_url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False
            )
            
            self.AsyncSessionLocal = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                autoflush=False,
                autocommit=False
            )
            logger.info("Async SQLAlchemy engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize async SQLAlchemy engine: {str(e)}")
            raise

    async def _init_tables(self):
        """
        Check if required tables exist and create them if they don't.
        Also ensures necessary constraints and indexes are in place.
        """
        try:
            async with self.AsyncSessionLocal() as session:
                # Check if tables exist
                result = await session.execute(text("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('agents', 'agent_versions', 'evaluations', 'evaluation_runs', 'weights_history', 'banned_hotkeys')
                """))
                existing_tables = [row[0] for row in result.fetchall()]
            
            logger.info(f"Existing database tables: {existing_tables}")

            required_tables = ['agent_versions', 'agents', 'evaluation_runs', 'evaluations', 'weights_history', 'banned_hotkeys']
            missing_tables = [table for table in required_tables if table not in existing_tables]
            
            if missing_tables:
                logger.info(f"Creating missing tables: {missing_tables}")

            # Create tables using SQLAlchemy
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            
            logger.info("Database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {str(e)}")
            raise

    async def close(self):
        """Close the database engine."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database engine closed")
        
    async def store_agent(self, agent: Agent) -> int:
        """Store an agent using async SQLAlchemy ORM"""
        async with self.AsyncSessionLocal() as session:
            try:
                # Use PostgreSQL's ON CONFLICT DO UPDATE
                stmt = insert(Agent).values(
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
                await session.execute(stmt)
                await session.commit()
                logger.info(f"Agent {agent.agent_id} stored successfully")
                return 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Error storing agent {agent.agent_id}: {str(e)}")
                return 0
        
    async def store_agent_version(self, agent_version: AgentVersion) -> int:
        """Store an agent version using async SQLAlchemy ORM"""
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = insert(AgentVersion).values(
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
                await session.execute(stmt)
                await session.commit()
                logger.info(f"Agent version {agent_version.version_id} stored successfully")
                return 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Error storing agent version {agent_version.version_id}: {str(e)}")
                return 0
    
    async def store_evaluation(self, evaluation: Evaluation) -> int:
        """Store an evaluation using async SQLAlchemy ORM"""
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = insert(Evaluation).values(
                    evaluation_id=evaluation.evaluation_id,
                    version_id=evaluation.version_id,
                    validator_hotkey=evaluation.validator_hotkey,
                    status=evaluation.status,
                    created_at=evaluation.created_at,
                    started_at=evaluation.started_at,
                    finished_at=evaluation.finished_at,
                    terminated_reason=evaluation.terminated_reason,
                    score=evaluation.score
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['evaluation_id'],
                    set_=dict(
                        status=stmt.excluded.status,
                        started_at=stmt.excluded.started_at,
                        finished_at=stmt.excluded.finished_at,
                        terminated_reason=stmt.excluded.terminated_reason,
                        score=stmt.excluded.score
                    )
                )
                await session.execute(stmt)
                await session.commit()
                logger.info(f"Evaluation {evaluation.evaluation_id} stored successfully")
                return 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Error storing evaluation {evaluation.evaluation_id}: {str(e)}")
                return 0
        
    async def update_agent_version_score(self, version_id: str) -> int:
        """Update the score for an agent version using async SQLAlchemy"""
        async with self.AsyncSessionLocal() as session:
            try:
                # Get average score from evaluations
                stmt = select(func.avg(Evaluation.score)).where(
                    and_(Evaluation.version_id == version_id, Evaluation.score.isnot(None))
                )
                result = await session.execute(stmt)
                avg_score = result.scalar()
                
                if avg_score is not None:
                    # Update agent version with average score
                    update_stmt = (
                        AgentVersion.__table__.update()
                        .where(AgentVersion.version_id == version_id)
                        .values(score=float(avg_score))
                    )
                    await session.execute(update_stmt)
                    await session.commit()
                    
                    logger.info(f"Updated agent version {version_id} with score {avg_score}")
                    return 1
                else:
                    logger.info(f"Tried to update agent version {version_id} with a score, but no scored evaluations found")
                    return 0
                    
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating agent version score for {version_id}: {str(e)}")
                return 0

    async def store_evaluation_run(self, evaluation_run: EvaluationRun) -> int:
        """
        Store an evaluation run in the database. Return 1 if successful, 0 if not.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = insert(EvaluationRun).values(
                    run_id=evaluation_run.run_id,
                    evaluation_id=evaluation_run.evaluation_id,
                    swebench_instance_id=evaluation_run.swebench_instance_id,
                    status=evaluation_run.status,
                    response=evaluation_run.response,
                    error=evaluation_run.error,
                    pass_to_fail_success=evaluation_run.pass_to_fail_success,
                    fail_to_pass_success=evaluation_run.fail_to_pass_success,
                    pass_to_pass_success=evaluation_run.pass_to_pass_success,
                    fail_to_fail_success=evaluation_run.fail_to_fail_success,
                    solved=evaluation_run.solved,
                    started_at=evaluation_run.started_at,
                    sandbox_created_at=evaluation_run.sandbox_created_at,
                    patch_generated_at=evaluation_run.patch_generated_at,
                    eval_started_at=evaluation_run.eval_started_at,
                    result_scored_at=evaluation_run.result_scored_at
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['run_id'],
                    set_=dict(
                        status=stmt.excluded.status,
                        response=stmt.excluded.response,
                        error=stmt.excluded.error,
                        pass_to_fail_success=stmt.excluded.pass_to_fail_success,
                        fail_to_pass_success=stmt.excluded.fail_to_pass_success,
                        pass_to_pass_success=stmt.excluded.pass_to_pass_success,
                        fail_to_fail_success=stmt.excluded.fail_to_fail_success,
                        solved=stmt.excluded.solved,
                        sandbox_created_at=stmt.excluded.sandbox_created_at,
                        patch_generated_at=stmt.excluded.patch_generated_at,
                        eval_started_at=stmt.excluded.eval_started_at,
                        result_scored_at=stmt.excluded.result_scored_at
                    )
                )
                await session.execute(stmt)
                await session.commit()
                logger.info(f"Evaluation run {evaluation_run.run_id} stored successfully")

                # Update the score for the associated evaluation
                update_stmt = (
                    Evaluation.__table__.update()
                    .where(Evaluation.evaluation_id == evaluation_run.evaluation_id)
                    .values(score=(
                        select(func.avg(func.cast(EvaluationRun.solved, Integer)))
                        .where(EvaluationRun.evaluation_id == evaluation_run.evaluation_id)
                        .scalar_subquery()
                    ))
                )
                await session.execute(update_stmt)
                await session.commit()
                logger.info(f"Updated score for evaluation {evaluation_run.evaluation_id}")

                return 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Error storing evaluation run {evaluation_run.run_id}: {str(e)}")
                return 0
        
    async def get_next_evaluation(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the next evaluation for a validator. Return None if not found.
        Excludes evaluations for banned miner hotkeys.
        """
        # For now, this is a manual process, but will be updated shortly to be automatic
        banned_hotkeys = ["5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9"]
        
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
                FROM evaluations e
                JOIN agent_versions av ON e.version_id = av.version_id
                JOIN agents a ON av.agent_id = a.agent_id
                WHERE e.validator_hotkey = :validator_hotkey 
                AND e.status = 'waiting' 
                AND a.miner_hotkey != ALL(:banned_hotkeys)
                ORDER BY e.created_at ASC 
                LIMIT 1;
            """), {'validator_hotkey': validator_hotkey, 'banned_hotkeys': banned_hotkeys})
                row = result.fetchone()
                if row:
                    logger.info(f"Next evaluation {row[0]} found for validator with hotkey {validator_hotkey}")
                    return Evaluation(
                        evaluation_id=row[0],
                        version_id=row[1],
                        validator_hotkey=row[2],
                        status=row[3],
                        terminated_reason=row[4],
                        created_at=row[5],
                        started_at=row[6],
                        finished_at=row[7],
                        score=row[8]
                    )
                logger.info(f"No pending evaluations found for validator with hotkey {validator_hotkey} (excluding banned miners)")
                return None
            except Exception as e:
                logger.error(f"Error getting next evaluation: {str(e)}")
                return None
        
    async def get_evaluation(self, evaluation_id: str) -> Optional[Evaluation]:
        """
        Get an evaluation from the database. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(Evaluation).where(Evaluation.evaluation_id == evaluation_id)
                result = await session.execute(stmt)
                evaluation = result.scalar_one_or_none()
                
                if evaluation:
                    logger.info(f"Evaluation {evaluation.evaluation_id} retrieved from the database")
                    return evaluation
                
                logger.info(f"Evaluation {evaluation_id} not found in the database")
                return None
            except Exception as e:
                logger.error(f"Error getting evaluation: {str(e)}")
                return None
        
    async def get_evaluation_run(self, run_id: str) -> Optional[EvaluationRun]:
        """
        Get an evaluation run from the database. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(EvaluationRun).where(EvaluationRun.run_id == run_id)
                result = await session.execute(stmt)
                evaluation_run = result.scalar_one_or_none()
                
                if evaluation_run:
                    return evaluation_run
                
                logger.info(f"Evaluation run {run_id} not found in the database")
                return None
            except Exception as e:
                logger.error(f"Error getting evaluation run: {str(e)}")
                return None
        
    async def delete_evaluation_runs(self, evaluation_id: str) -> int:
        """
        Delete all evaluation runs for a specific evaluation. Return the number of deleted runs.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    DELETE FROM evaluation_runs WHERE evaluation_id = :evaluation_id
                """), {'evaluation_id': evaluation_id})
                deleted_count = result.rowcount
                logger.info(f"Deleted {deleted_count} evaluation runs for evaluation {evaluation_id}")
                return deleted_count
            except Exception as e:
                logger.error(f"Error deleting evaluation runs: {str(e)}")
                return 0
        
    async def get_agent_by_hotkey(self, miner_hotkey: str) -> Optional[Agent]:
        """
        Get an agent from the database. Return None if not found.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(Agent).where(Agent.miner_hotkey == miner_hotkey)
                result = await session.execute(stmt)
                agent = result.scalar_one_or_none()
                return agent
            except Exception as e:
                logger.error(f"Error getting agent by hotkey: {str(e)}")
                return None
        
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """
        Get an agent from the database. Return None if not found.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(Agent).where(Agent.agent_id == agent_id)
                result = await session.execute(stmt)
                agent = result.scalar_one_or_none()
                return agent
            except Exception as e:
                logger.error(f"Error getting agent: {str(e)}")
                return None
        
    async def get_random_agent(self) -> Optional[AgentSummary]:
        """
        Get a random agent from the database. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT agent_id, miner_hotkey, name, latest_version
                    FROM agents
                    ORDER BY RANDOM()
                    LIMIT 1
                """))
                row = result.fetchone()
                
                if not row:
                    return None
                
                agent_id, miner_hotkey, name, latest_version_num = row
                
                result = await session.execute(text("""
                    SELECT version_id, agent_id, version_num, created_at, score
                    FROM agent_versions
                    WHERE agent_id = :agent_id
                    ORDER BY version_num DESC
                    LIMIT 1
                """), {'agent_id': agent_id})
                
                version_row = result.fetchone()
                if version_row:
                    agent_version = AgentVersionResponse(
                        version_id=str(version_row[0]),
                        agent_id=str(version_row[1]),
                        version_num=version_row[2],
                        created_at=version_row[3],
                        score=version_row[4]
                    )
                    return AgentSummary(
                        miner_hotkey=miner_hotkey,
                        name=name,
                        latest_version=agent_version,
                        code=None
                    )
                return None
            except Exception as e:
                logger.error(f"Error getting random agent: {str(e)}")
                return None
        
    async def get_agent_by_version_id(self, version_id: str) -> Optional[Agent]:
        """
        Get an agent from the database by version_id. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT a.agent_id, a.miner_hotkey, a.name, a.latest_version, a.created_at, a.last_updated 
                    FROM agents a
                    JOIN agent_versions av ON a.agent_id = av.agent_id
                    WHERE av.version_id = :version_id
                """), {'version_id': version_id})
                row = result.fetchone()
                if row:
                    return Agent(
                        agent_id=row[0],
                        miner_hotkey=row[1],
                        name=row[2],
                        latest_version=row[3],
                        created_at=row[4],
                        last_updated=row[5]
                    )
                return None
            except Exception as e:
                logger.error(f"Error getting agent by version_id: {str(e)}")
                return None
        
    async def get_evaluations_by_version_id(self, version_id: str) -> List[Evaluation]:
        """
        Get all evaluations for a version from the database. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
                    FROM evaluations WHERE version_id = :version_id
                """), {'version_id': version_id})
                rows = result.fetchall()
                return [Evaluation(
                    evaluation_id=row[0],
                    version_id=row[1],
                    validator_hotkey=row[2],
                    status=row[3],
                    terminated_reason=row[4],
                    created_at=row[5],
                    started_at=row[6],
                    finished_at=row[7],
                    score=row[8]
                ) for row in rows]
            except Exception as e:
                logger.error(f"Error getting evaluations by version_id: {str(e)}")
                return None
        
    async def get_agent_version(self, version_id: str) -> Optional[AgentVersion]:
        """
        Get an agent version from the database. Return None if not found.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(AgentVersion).where(AgentVersion.version_id == version_id)
                result = await session.execute(stmt)
                agent_version = result.scalar_one_or_none()
                return agent_version
            except Exception as e:
                logger.error(f"Error getting agent version: {str(e)}")
                return None
        
    async def get_latest_agent_version(self, agent_id: str) -> Optional[AgentVersion]:
        """
        Get the latest agent version from the database. Return None if not found.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(AgentVersion).where(AgentVersion.agent_id == agent_id).order_by(AgentVersion.created_at.desc()).limit(1)
                result = await session.execute(stmt)
                agent_version = result.scalar_one_or_none()
                return agent_version
            except Exception as e:
                logger.error(f"Error getting latest agent version: {str(e)}")
                return None

    async def get_current_evaluations(self) -> List[RunningAgentEval]:
        """Get all currently running evaluations using async SQLAlchemy."""
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT
                        e.version_id, e.validator_hotkey, e.started_at,
                        v.agent_id, v.version_num,
                        a.miner_hotkey, a.name
                    FROM evaluations e
                    LEFT JOIN agent_versions v ON v.version_id = e.version_id
                    LEFT JOIN agents a ON a.agent_id = v.agent_id
                    WHERE e.status = 'running'
                """))
                
                rows = result.fetchall()
                
                return [RunningAgentEval(
                    version_id=str(row[0]),
                    validator_hotkey=row[1],
                    started_at=row[2],
                    agent_id=str(row[3]),
                    version_num=row[4],
                    miner_hotkey=row[5],
                    name=row[6]
                ) for row in rows]
            except Exception as e:
                logger.error(f"Error getting current evaluations: {str(e)}")
                return []
        
    async def get_running_evaluation_by_validator_hotkey(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the running evaluation for a validator. Return None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = select(Evaluation).where(
                    and_(Evaluation.validator_hotkey == validator_hotkey, Evaluation.status == 'running')
                )
                result = await session.execute(stmt)
                evaluation = result.scalar_one_or_none()
                return evaluation
            except Exception as e:
                logger.error(f"Error getting running evaluation: {str(e)}")
                return None
        
    async def get_top_agents(self, num_agents: int) -> List[AgentSummary]:
        """
        Get the top agents from the database based on their latest scored version's score.
        Returns agents ordered by their latest version's score in descending order first,
        followed by all unscored agents.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT * FROM (
                        SELECT 
                            a.agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at,
                            a.last_updated,
                            COALESCE(latest_scored.version_id, latest_ver.version_id) as version_id,
                            COALESCE(latest_scored.version_num, latest_ver.version_num) as version_num,
                            COALESCE(latest_scored.created_at, latest_ver.created_at) as version_created_at,
                            latest_scored.score,
                            CASE WHEN latest_scored.score IS NOT NULL THEN 1 ELSE 2 END as sort_order
                        FROM agents a
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            WHERE score IS NOT NULL
                            ORDER BY agent_id, created_at DESC
                        ) latest_scored ON a.agent_id = latest_scored.agent_id
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            ORDER BY agent_id, created_at DESC
                        ) latest_ver ON a.agent_id = latest_ver.agent_id
                    ) combined_results
                    ORDER BY sort_order, score DESC NULLS LAST
                    LIMIT :num_agents
                """), {'num_agents': num_agents})
                rows = result.fetchall()
                return [AgentSummary(
                    miner_hotkey=row[1],
                    name=row[2],
                    latest_version=AgentVersionResponse(
                        version_id=str(row[6]),
                        agent_id=str(row[0]),
                        version_num=row[7],
                        created_at=row[8],
                        score=row[9]
                    ),
                    code=None
                ) for row in rows]
            except Exception as e:
                logger.error(f"Error getting top agents: {str(e)}")
                return []
        
    async def get_top_agent(self) -> TopAgentHotkey:
        """
        Gets the top agents miner hotkey and version id from the database,
        where its been scored by at least 2 validators
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    WITH version_scores AS (                         -- 1.  score + validator count
                        SELECT
                            e.version_id,
                            AVG(e.score)                       AS avg_score,      -- use MAX() if preferred
                            COUNT(DISTINCT e.validator_hotkey) AS validator_cnt
                        FROM evaluations e
                        WHERE e.status = 'completed'
                        AND e.score  IS NOT NULL
                        GROUP BY e.version_id
                        HAVING COUNT(DISTINCT e.validator_hotkey) >= 1
                    ),

                    top_score AS (                                  -- 2.  the absolute best score
                        SELECT MAX(avg_score) AS max_score
                        FROM version_scores
                    ),

                    close_enough AS (                               -- 3.  scores ≥ 98 % of the best
                        SELECT
                            vs.version_id,
                            vs.avg_score,
                            av.created_at,
                            ROW_NUMBER() OVER (ORDER BY av.created_at ASC) AS rn  -- oldest first
                        FROM version_scores vs
                        JOIN agent_versions av ON av.version_id = vs.version_id
                        CROSS JOIN top_score ts
                        WHERE vs.avg_score >= ts.max_score * 0.98    -- within 2 %
                    )

                    SELECT
                        a.miner_hotkey,
                        ce.version_id,
                        ce.avg_score
                    FROM close_enough   ce
                    JOIN agent_versions av ON av.version_id = ce.version_id
                    JOIN agents         a  ON a.agent_id    = av.agent_id
                    WHERE ce.rn = 1;
                    """))

                row = result.fetchone()

                if row is None:
                    return None

                return TopAgentHotkey(
                    miner_hotkey=row[0],
                    version_id=str(row[1]),
                    avg_score=row[2]
                )
            except Exception as e:
                logger.error(f"Error getting top agent: {str(e)}")
                return None
        
    async def get_latest_agent(self, agent_id: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                if scored:
                    result = await session.execute(text("""
                        SELECT 
                            a.agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at,
                            a.last_updated,
                            latest_scored.version_id,
                            latest_scored.version_num,
                            latest_scored.created_at as version_created_at,
                            latest_scored.score
                        FROM agents a
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            WHERE score IS NOT NULL
                            ORDER BY agent_id, created_at DESC
                        ) latest_scored ON a.agent_id = latest_scored.agent_id
                        WHERE a.agent_id = :agent_id
                        AND latest_scored.score IS NOT NULL
                    """), {'agent_id': agent_id})
                else:
                    result = await session.execute(text("""
                        SELECT 
                            a.agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at,
                            a.last_updated,
                            latest_ver.version_id,
                            latest_ver.version_num,
                            latest_ver.created_at as version_created_at,
                            latest_ver.score
                        FROM agents a
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            ORDER BY agent_id, created_at DESC
                        ) latest_ver ON a.agent_id = latest_ver.agent_id
                        WHERE a.agent_id = :agent_id
                    """), {'agent_id': agent_id})
                
                row = result.fetchone()
                if row:
                    return AgentSummary(
                        miner_hotkey=row[1],
                        name=row[2],
                        latest_version=AgentVersionResponse(
                            version_id=str(row[6]),
                            agent_id=str(row[0]),
                            version_num=row[7],
                            created_at=row[8],
                            score=row[9]
                        ),
                        code=None
                    )
                return None
            except Exception as e:
                logger.error(f"Error getting latest agent: {str(e)}")
                return None
        
    async def get_latest_agent_by_miner_hotkey(self, miner_hotkey: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database by miner_hotkey. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                if scored:
                    result = await session.execute(text("""
                        SELECT 
                            a.agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at,
                            a.last_updated,
                            latest_scored.version_id,
                            latest_scored.version_num,
                            latest_scored.created_at as version_created_at,
                            latest_scored.score
                        FROM agents a
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            WHERE score IS NOT NULL
                            ORDER BY agent_id, created_at DESC
                        ) latest_scored ON a.agent_id = latest_scored.agent_id
                        WHERE a.miner_hotkey = :miner_hotkey
                        AND latest_scored.score IS NOT NULL
                    """), {'miner_hotkey': miner_hotkey})
                else:
                    result = await session.execute(text("""
                        SELECT 
                            a.agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at,
                            a.last_updated,
                            latest_ver.version_id,
                            latest_ver.version_num,
                            latest_ver.created_at as version_created_at,
                            latest_ver.score
                        FROM agents a
                        LEFT JOIN (
                            SELECT DISTINCT ON (agent_id) 
                                agent_id,
                                version_id,
                                version_num,
                                created_at,
                                score
                            FROM agent_versions 
                            ORDER BY agent_id, created_at DESC
                        ) latest_ver ON a.agent_id = latest_ver.agent_id
                        WHERE a.miner_hotkey = :miner_hotkey
                    """), {'miner_hotkey': miner_hotkey})
                
                row = result.fetchone()
                if row:
                    return AgentSummary(
                        miner_hotkey=row[1],
                        name=row[2],
                        latest_version=AgentVersionResponse(
                            version_id=str(row[6]),
                            agent_id=str(row[0]),
                            version_num=row[7],
                            created_at=row[8],
                            score=row[9]
                        ),
                        code=None
                    )
                return None
            except Exception as e:
                logger.error(f"Error getting latest agent by miner_hotkey: {str(e)}")
                return None
        
    async def get_recent_executions(self, num_executions: int) -> List[Execution]:
        """
        Gets the X most recently created evaluations, and returns a list of objects with the AgentVersion, Agent, Evaluation, and Runs
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 
                        e.evaluation_id,
                        e.version_id,
                        e.validator_hotkey,
                        e.status,
                        e.terminated_reason,
                        e.created_at,
                        e.started_at,
                        e.finished_at,
                        e.score,
                        av.version_id as agent_version_id,
                        av.agent_id,
                        av.version_num,
                        av.created_at as agent_version_created_at,
                        av.score,
                        a.agent_id as agent_agent_id,
                        a.miner_hotkey,
                        a.name,
                        a.latest_version,
                        a.created_at as agent_created_at,
                        a.last_updated
                    FROM evaluations e
                    JOIN agent_versions av ON e.version_id = av.version_id
                    JOIN agents a ON av.agent_id = a.agent_id
                    ORDER BY e.created_at DESC
                    LIMIT :num_executions
                """), {'num_executions': num_executions})
                
                rows = result.fetchall()
                executions = []
                
                for row in rows:
                    evaluation_id = row[0]
                    
                    # Get evaluation runs for this evaluation
                    result = await session.execute(text("""
                        SELECT 
                            run_id,
                            evaluation_id,
                            swebench_instance_id,
                            status,
                            response,
                            error,
                            pass_to_fail_success,
                            fail_to_pass_success,
                            pass_to_pass_success,
                            fail_to_fail_success,
                            solved,
                            started_at,
                            sandbox_created_at,
                            patch_generated_at,
                            eval_started_at,
                            result_scored_at
                        FROM evaluation_runs 
                        WHERE evaluation_id = :evaluation_id
                    """), {'evaluation_id': evaluation_id})
                    
                    run_rows = result.fetchall()
                    evaluation_runs = [
                        EvaluationRunResponse(
                            run_id=str(run_row[0]),
                            evaluation_id=str(run_row[1]),
                            swebench_instance_id=run_row[2],
                            status=run_row[3],
                            response=run_row[4],
                            error=run_row[5],
                            pass_to_fail_success=run_row[6],
                            fail_to_pass_success=run_row[7],
                            pass_to_pass_success=run_row[8],
                            fail_to_fail_success=run_row[9],
                            solved=run_row[10],
                            started_at=run_row[11],
                            sandbox_created_at=run_row[12],
                            patch_generated_at=run_row[13],
                            eval_started_at=run_row[14],
                            result_scored_at=run_row[15]
                        ) for run_row in run_rows
                    ]
                    
                    # Create Execution object
                    execution = Execution(
                        evaluation=EvaluationResponse(
                            evaluation_id=str(row[0]),
                            version_id=str(row[1]),
                            validator_hotkey=row[2],
                            status=row[3],
                            terminated_reason=row[4],
                            created_at=row[5],
                            started_at=row[6],
                            finished_at=row[7],
                            score=row[8]
                        ),
                        evaluation_runs=evaluation_runs,
                        agent=AgentResponse(
                            agent_id=str(row[14]),
                            miner_hotkey=row[15],
                            name=row[16],
                            latest_version=row[17],
                            created_at=row[18],
                            last_updated=row[19]
                        ),
                        agent_version=AgentVersionResponse(
                            version_id=str(row[9]),
                            agent_id=str(row[10]),
                            version_num=row[11],
                            created_at=row[12],
                            score=row[13]
                        )
                    )
                    executions.append(execution)
                
                return executions
            except Exception as e:
                logger.error(f"Error getting recent executions: {str(e)}")
                return []
        
    async def get_num_agents(self) -> int:
        """
        Get the number of agents in the database.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT COUNT(*) FROM agents
                """))
                return result.scalar()
            except Exception as e:
                logger.error(f"Error getting agent count: {str(e)}")
                return 0
        
    async def get_num_agent_versions(self) -> int:
        """
        Get the number of agent versions in the database.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("SELECT COUNT(*) FROM agent_versions"))
                return result.scalar()
            except Exception as e:
                logger.error(f"Error getting agent version count: {str(e)}")
                return 0
        
    async def get_num_evaluations(self) -> int:
        """
        Get the number of evaluations in the database.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("SELECT COUNT(*) FROM evaluations"))
                return result.scalar()
            except Exception as e:
                logger.error(f"Error getting evaluation count: {str(e)}")
                return 0
        
    async def get_num_evaluation_runs(self) -> int:
        """
        Get the number of evaluation runs in the database.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("SELECT COUNT(*) FROM evaluation_runs"))
                return result.scalar()
            except Exception as e:
                logger.error(f"Error getting evaluation run count: {str(e)}")
                return 0
        
    async def get_evaluation_status_counts(self) -> dict:
        """
        Get counts of evaluations grouped by status.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT status, COUNT(*) 
                    FROM evaluations 
                    GROUP BY status
                """))
                return dict(result.fetchall())
            except Exception as e:
                logger.error(f"Error getting evaluation status counts: {str(e)}")
                return {}
        
    async def get_evaluation_run_status_counts(self) -> dict:
        """
        Get counts of evaluation runs grouped by status.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT status, COUNT(*) 
                    FROM evaluation_runs 
                    GROUP BY status
                """))
                return dict(result.fetchall())
            except Exception as e:
                logger.error(f"Error getting evaluation run status counts: {str(e)}")
                return {}
        
    async def get_database_summary(self) -> dict:
        """
        Get a summary of row counts for all tables.
        """
        return {
            'agents': await self.get_num_agents(),
            'agent_versions': await self.get_num_agent_versions(),
            'evaluations': await self.get_num_evaluations(),
            'evaluation_runs': await self.get_num_evaluation_runs(),
            'banned_hotkeys': await self._count_table_rows('banned_hotkeys'),
            'weights_history': await self._count_table_rows('weights_history')
        }

    async def _count_table_rows(self, table_name: str) -> int:
        """
        Helper method to count rows in any table.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                return result.scalar()
            except Exception as e:
                logger.error(f"Error counting rows in {table_name}: {str(e)}")
                return 0
        
    async def get_latest_execution_by_agent(self, agent_id: str) -> Optional[Execution]:
        """
        Get the current execution for an agent with priority:
        1. Running evaluation (if any)
        2. Most recent scored evaluation (if any)  
        3. None if neither exists
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # First, try to get the most recent running evaluation
                result = await session.execute(text("""
                    SELECT 
                        e.evaluation_id,
                        e.version_id,
                        e.validator_hotkey,
                        e.status,
                        e.terminated_reason,
                        e.created_at,
                        e.started_at,
                        e.finished_at,
                        e.score,
                        av.version_id as av_version_id,
                        av.agent_id,
                        av.version_num,
                        av.created_at as agent_version_created_at,
                        av.score,
                        a.agent_id as agent_agent_id,
                        a.miner_hotkey,
                        a.name,
                        a.latest_version,
                        a.created_at as agent_created_at,
                        a.last_updated
                    FROM evaluations e
                    JOIN agent_versions av ON e.version_id = av.version_id
                    JOIN agents a ON av.agent_id = a.agent_id
                    WHERE a.agent_id = :agent_id AND e.status = 'running'
                    ORDER BY e.created_at DESC 
                    LIMIT 1
                """), {'agent_id': agent_id})
                
                row = result.fetchone()
                
                # If no running evaluation, get the most recent scored evaluation
                if not row:
                    result = await session.execute(text("""
                        SELECT 
                            e.evaluation_id,
                            e.version_id,
                            e.validator_hotkey,
                            e.status,
                            e.terminated_reason,
                            e.created_at,
                            e.started_at,
                            e.finished_at,
                            e.score,
                            av.version_id as av_version_id,
                            av.agent_id,
                            av.version_num,
                            av.created_at as agent_version_created_at,
                            av.score,
                            a.agent_id as agent_agent_id,
                            a.miner_hotkey,
                            a.name,
                            a.latest_version,
                            a.created_at as agent_created_at,
                            a.last_updated
                        FROM evaluations e
                        JOIN agent_versions av ON e.version_id = av.version_id
                        JOIN agents a ON av.agent_id = a.agent_id
                        WHERE a.agent_id = :agent_id AND av.score IS NOT NULL
                        ORDER BY e.created_at DESC 
                        LIMIT 1
                    """), {'agent_id': agent_id})
                    
                    row = result.fetchone()
                
                # If still no row found, return None
                if not row:
                    return None
                
                evaluation_id = row[0]
                
                # Get evaluation runs for this evaluation
                result = await session.execute(text("""
                    SELECT 
                        run_id,
                        evaluation_id,
                        swebench_instance_id,
                        status,
                        response,
                        error,
                        pass_to_fail_success,
                        fail_to_pass_success,
                        pass_to_pass_success,
                        fail_to_fail_success,
                        solved,
                        started_at,
                        sandbox_created_at,
                        patch_generated_at,
                        eval_started_at,
                        result_scored_at
                    FROM evaluation_runs 
                    WHERE evaluation_id = :evaluation_id
                """), {'evaluation_id': evaluation_id})
                
                run_rows = result.fetchall()
                evaluation_runs = [
                    EvaluationRun(
                        run_id=run_row[0],
                        evaluation_id=run_row[1],
                        swebench_instance_id=run_row[2],
                        status=run_row[3],
                        response=run_row[4],
                        error=run_row[5],
                        pass_to_fail_success=run_row[6],
                        fail_to_pass_success=run_row[7],
                        pass_to_pass_success=run_row[8],
                        fail_to_fail_success=run_row[9],
                        solved=run_row[10],
                        started_at=run_row[11],
                        sandbox_created_at=run_row[12],
                        patch_generated_at=run_row[13],
                        eval_started_at=run_row[14],
                        result_scored_at=run_row[15]
                    ) for run_row in run_rows
                ]
                
                # Create Execution object
                execution = Execution(
                    evaluation=Evaluation(
                        evaluation_id=row[0],
                        version_id=row[1],
                        validator_hotkey=row[2],
                        status=row[3],
                        terminated_reason=row[4],
                        created_at=row[5],
                        started_at=row[6],
                        finished_at=row[7],
                        score=row[8]
                    ),
                    evaluation_runs=evaluation_runs,
                    agent=Agent(
                        agent_id=row[14],
                        miner_hotkey=row[15],
                        name=row[16],
                        latest_version=row[17],
                        created_at=row[18],
                        last_updated=row[19]
                    ),
                    agent_version=AgentVersion(
                        version_id=row[9],
                        agent_id=row[10],
                        version_num=row[11],
                        created_at=row[12],
                        score=row[13]
                    )
                )
                
                return execution
            except Exception as e:
                logger.error(f"Error getting latest execution: {str(e)}")
                return None
        
    async def get_agent_summary(self, agent_id: str = None, miner_hotkey: str = None) -> AgentSummaryResponse:
        """
        Get a summary of an agent including its details, latest version, and all versions.
        Returns AgentSummaryResponse with agent_details, latest_version, and all_versions.
        Uses optimized single-query approach with JSON aggregation.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # Use agent_id if provided, otherwise use miner_hotkey
                if agent_id:
                    search_param = agent_id
                    search_type = "ID"
                    where_condition = "agent_id = :search_param"
                else:
                    search_param = miner_hotkey
                    search_type = "miner hotkey"
                    where_condition = "miner_hotkey = :search_param"
                
                # Build the SQL query dynamically with the appropriate WHERE condition
                sql_query = f"""
                    WITH a AS (
                        SELECT agent_id, miner_hotkey, name, created_at
                        FROM   agents
                        WHERE  {where_condition}
                    )
                    SELECT
                        a.*,

                        /* latest_version – 1 row, picked with LIMIT 1 */
                        (
                          SELECT jsonb_build_object(
                                     'version_id',  av.version_id,
                                     'version_num', av.version_num,
                                     'created_at',  av.created_at,
                                     'score',       COALESCE(avg_score.avg_evaluation_score, av.score)
                                 )
                          FROM   agent_versions av
                          LEFT JOIN (
                              SELECT version_id, AVG(score) as avg_evaluation_score
                              FROM evaluations 
                              WHERE status = 'completed' AND score IS NOT NULL
                              GROUP BY version_id
                          ) avg_score ON av.version_id = avg_score.version_id
                          WHERE  av.agent_id = a.agent_id
                          ORDER  BY av.version_num DESC
                          LIMIT  1
                        )                                                AS latest_version,

                        /* all_versions as an ordered JSON array */
                        (
                          SELECT jsonb_agg(jsonb_build_object(
                                     'version_id',  av.version_id,
                                     'version_num', av.version_num,
                                     'created_at',  av.created_at,
                                     'score',       COALESCE(avg_score.avg_evaluation_score, av.score)
                                 ) ORDER BY av.version_num DESC)
                          FROM   agent_versions av
                          LEFT JOIN (
                              SELECT version_id, AVG(score) as avg_evaluation_score
                              FROM evaluations 
                              WHERE status = 'completed' AND score IS NOT NULL
                              GROUP BY version_id
                          ) avg_score ON av.version_id = avg_score.version_id
                          WHERE  av.agent_id = a.agent_id
                        )                                                AS all_versions
                    FROM a;
                """
                
                result = await session.execute(text(sql_query), {'search_param': search_param})
                
                row = result.fetchone()
                if not row:
                    raise ValueError(f"Agent with {search_type} {search_param} not found")
                
                # Parse the result
                agent_id, miner_hotkey, name, created_at, latest_version_json, all_versions_json = row
                
                # Create agent details
                agent_details = AgentDetailsNew(
                    agent_id=str(agent_id),
                    miner_hotkey=miner_hotkey,
                    name=name,
                    created_at=created_at
                )
                
                # Parse latest version from JSON
                latest_version = None
                if latest_version_json:
                    latest_version = AgentVersionNew(
                        version_id=str(latest_version_json['version_id']),
                        version_num=latest_version_json['version_num'],
                        created_at=latest_version_json['created_at'],
                        score=latest_version_json['score'],
                        code=None
                    )
                
                # Parse all versions from JSON array
                all_versions = []
                if all_versions_json:
                    for version_data in all_versions_json:
                        version = AgentVersionNew(
                            version_id=str(version_data['version_id']),
                            version_num=version_data['version_num'],
                            created_at=version_data['created_at'],
                            score=version_data['score'],
                            code=None
                        )
                        all_versions.append(version)
                
                return AgentSummaryResponse(
                    agent_details=agent_details,
                    latest_version=latest_version,
                    all_versions=all_versions
                )
                
            except Exception as e:
                logger.error(f"Error getting agent summary: {str(e)}")
                return None
        
    async def get_agent_version_new(self, version_id: str) -> AgentVersionDetails:
        """
        Get detailed information about an agent version including its execution data.
        Returns AgentVersionDetails with agent_version and execution.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT version_id, version_num, created_at, score
                    FROM agent_versions 
                    WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                version_row = result.fetchone()
                if not version_row:
                    raise ValueError(f"Agent version with ID {version_id} not found")
                
                agent_version = AgentVersionNew(
                    version_id=str(version_row[0]),
                    version_num=version_row[1],
                    created_at=version_row[2],
                    score=version_row[3],
                    code=None
                )
                
                result = await session.execute(text("""
                    SELECT 
                        evaluation_id,
                        version_id,
                        validator_hotkey,
                        status,
                        terminated_reason,
                        created_at,
                        started_at,
                        finished_at,
                        score
                    FROM evaluations 
                    WHERE version_id = :version_id
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {'version_id': version_id})
                
                execution_row = result.fetchone()
                if not execution_row:
                    raise ValueError(f"No execution found for version {version_id}")
                
                evaluation_id = execution_row[0]
                
                result = await session.execute(text("""
                    SELECT 
                        run_id,
                        evaluation_id,
                        swebench_instance_id,
                        status,
                        response,
                        error,
                        pass_to_fail_success,
                        fail_to_pass_success,
                        pass_to_pass_success,
                        fail_to_fail_success,
                        solved,
                        started_at,
                        sandbox_created_at,
                        patch_generated_at,
                        eval_started_at,
                        result_scored_at
                    FROM evaluation_runs 
                    WHERE evaluation_id = :evaluation_id
                    ORDER BY started_at
                """), {'evaluation_id': evaluation_id})
                
                run_rows = result.fetchall()
                evaluation_runs = [
                    EvaluationRunResponse(
                        run_id=str(run_row[0]),
                        evaluation_id=str(run_row[1]),
                        swebench_instance_id=run_row[2],
                        status=run_row[3],
                        response=run_row[4],
                        error=run_row[5],
                        pass_to_fail_success=run_row[6],
                        fail_to_pass_success=run_row[7],
                        pass_to_pass_success=run_row[8],
                        fail_to_fail_success=run_row[9],
                        solved=run_row[10],
                        started_at=run_row[11],
                        sandbox_created_at=run_row[12],
                        patch_generated_at=run_row[13],
                        eval_started_at=run_row[14],
                        result_scored_at=run_row[15]
                    ) for run_row in run_rows
                ]
                
                execution = ExecutionNew(
                    evaluation_id=str(execution_row[0]),
                    agent_version_id=str(execution_row[1]),
                    validator_hotkey=execution_row[2],
                    status=execution_row[3],
                    terminated_reason=execution_row[4],
                    created_at=execution_row[5],
                    started_at=execution_row[6],
                    finished_at=execution_row[7],
                    score=execution_row[8],
                    evaluation_runs=evaluation_runs
                )
                
                return AgentVersionDetails(
                    agent_version=agent_version,
                    execution=execution
                )
                
            except Exception as e:
                logger.error(f"Error getting agent version details: {str(e)}")
                return None
        
    async def get_evaluations(self, version_id: str) -> List[ExecutionNew]:
        """
        Get all evaluations for a specific agent version, including their evaluation runs.
        Returns a list of ExecutionNew objects.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 
                        evaluation_id,
                        version_id,
                        validator_hotkey,
                        status,
                        terminated_reason,
                        created_at,
                        started_at,
                        finished_at,
                        score
                    FROM evaluations 
                    WHERE version_id = :version_id
                    ORDER BY created_at DESC
                """), {'version_id': version_id})
                
                evaluation_rows = result.fetchall()
                executions = []
                
                for eval_row in evaluation_rows:
                    evaluation_id = eval_row[0]
                    
                    result = await session.execute(text("""
                        SELECT 
                            run_id,
                            evaluation_id,
                            swebench_instance_id,
                            status,
                            response,
                            error,
                            pass_to_fail_success,
                            fail_to_pass_success,
                            pass_to_pass_success,
                            fail_to_fail_success,
                            solved,
                            started_at,
                            sandbox_created_at,
                            patch_generated_at,
                            eval_started_at,
                            result_scored_at
                        FROM evaluation_runs 
                        WHERE evaluation_id = :evaluation_id
                        ORDER BY started_at
                    """), {'evaluation_id': evaluation_id})
                    
                    run_rows = result.fetchall()
                    evaluation_runs = [
                        EvaluationRunResponse(
                            run_id=str(run_row[0]),
                            evaluation_id=str(run_row[1]),
                            swebench_instance_id=run_row[2],
                            status=run_row[3],
                            response=run_row[4],
                            error=run_row[5],
                            pass_to_fail_success=run_row[6],
                            fail_to_pass_success=run_row[7],
                            pass_to_pass_success=run_row[8],
                            fail_to_fail_success=run_row[9],
                            solved=run_row[10],
                            started_at=run_row[11],
                            sandbox_created_at=run_row[12],
                            patch_generated_at=run_row[13],
                            eval_started_at=run_row[14],
                            result_scored_at=run_row[15]
                        ) for run_row in run_rows
                    ]
                    
                    # Create ExecutionNew object
                    execution = ExecutionNew(
                        evaluation_id=str(eval_row[0]),
                        agent_version_id=str(eval_row[1]),
                        validator_hotkey=eval_row[2],
                        status=eval_row[3],
                        terminated_reason=eval_row[4],
                        created_at=eval_row[5],
                        started_at=eval_row[6],
                        finished_at=eval_row[7],
                        score=eval_row[8],
                        evaluation_runs=evaluation_runs
                    )
                    
                    executions.append(execution)
                
                return executions
                
            except Exception as e:
                logger.error(f"Error getting evaluations: {str(e)}")
                return []
        
    async def store_weights(self, miner_weights: dict, time_since_last_update=None) -> int:
        """
        Store miner weights in the weights_history table. Return 1 if successful, 0 if not.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = insert(WeightsHistory).values(
                    timestamp=datetime.now(),
                    time_since_last_update=time_since_last_update,
                    miner_weights=json.dumps(miner_weights)
                )
                await session.execute(stmt)
                await session.commit()
                logger.info(f"Weights stored successfully with {len(miner_weights)} miners")
                return 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Error storing weights: {str(e)}")
                return 0
        
    async def get_latest_weights(self) -> Optional[dict]:
        """
        Get the most recent weights from the weights_history table. Return None if not found.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT miner_weights, timestamp, time_since_last_update
                    FROM weights_history 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """))
                row = result.fetchone()
                
                if row:
                    return {
                        'weights': json.loads(row[0]),  # JSONB is already a dict, no need to parse
                        'timestamp': row[1],
                        'time_since_last_update': row[2]
                    }
                return None
            except Exception as e:
                logger.error(f"Error getting latest weights: {str(e)}")
                return None
        
    def weights_are_different(self, current_weights: dict, stored_weights: dict) -> bool:
        """
        Compare current weights with stored weights to check if they're different.
        Returns True if weights are different, False if they're the same.
        """
        try:
            current_uids = set(str(uid) for uid in current_weights.keys())
            stored_uids = set(str(uid) for uid in stored_weights.keys())
            
            if current_uids != stored_uids:
                added_miners = current_uids - stored_uids
                removed_miners = stored_uids - current_uids
                
                if added_miners:
                    logger.info(f"Added miners: {added_miners}. Updating weights in database.")
                if removed_miners:
                    logger.info(f"Removed miners: {removed_miners}. Updating weights in database.")
                
                return True
            
            # Check if any weights have changed (with small tolerance for floating point)
            tolerance = 1e-6
            for uid in current_weights.keys():
                current_weight = current_weights[uid]
                stored_weight = stored_weights.get(str(uid))
                
                if stored_weight is None:
                    logger.info(f"UID {uid} not found in stored weights. Updating weights in database.")
                    return True
                
                if abs(current_weight - stored_weight) > tolerance:
                    logger.info(f"Weight changed for UID {uid}: {stored_weight} -> {current_weight}. Updating database.")
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Error comparing weights: {str(e)}")
            return False

    async def get_top_miner_fraction_last_24h(self, miner_hotkey: str) -> float:
        """
        Calculate the fraction of the last 24 hours that a miner held the top weight position.
        Returns a value between 0.0 and 1.0 representing the fraction of time they were the top miner.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    WITH weight_periods AS (
                        -- Get all weight snapshots from the last 24 hours
                        SELECT 
                            timestamp,
                            miner_weights,
                            LAG(timestamp) OVER (ORDER BY timestamp) as prev_timestamp
                        FROM weights_history 
                        WHERE timestamp >= NOW() - INTERVAL '24 hours'
                        ORDER BY timestamp
                    ),
                    top_miner_periods AS (
                        -- For each period, identify who was the top miner
                        SELECT 
                            timestamp,
                            prev_timestamp,
                            -- Extract the miner with the highest weight for each snapshot
                            (SELECT key FROM jsonb_each(miner_weights) 
                             ORDER BY value::float DESC 
                             LIMIT 1) as top_miner_hotkey,
                            -- Calculate the duration of this period
                            CASE 
                                WHEN prev_timestamp IS NULL THEN 
                                    EXTRACT(EPOCH FROM (timestamp - (NOW() - INTERVAL '24 hours'))) / 86400.0
                                ELSE 
                                    EXTRACT(EPOCH FROM (timestamp - prev_timestamp)) / 86400.0
                            END as period_duration
                        FROM weight_periods
                    ),
                    miner_top_time AS (
                        -- Sum up the time periods where this miner was on top
                        SELECT 
                            SUM(period_duration) as total_top_time
                        FROM top_miner_periods 
                        WHERE top_miner_hotkey = :miner_hotkey
                    )
                    SELECT COALESCE(total_top_time, 0.0) as fraction
                    FROM miner_top_time
                """), {'miner_hotkey': miner_hotkey})
                
                result = result.fetchone()
                if result:
                    fraction = float(result[0])
                    logger.info(f"Miner {miner_hotkey} was top miner for {fraction:.4f} fraction of the last 24 hours")
                    return fraction
                else:
                    logger.warning(f"No weight history found for miner {miner_hotkey} in the last 24 hours")
                    return 0.0
                    
            except Exception as e:
                logger.error(f"Error calculating top miner fraction: {str(e)}")
                return 0.0
        
    async def get_current_top_miner(self) -> Optional[str]:
        """
        Get the miner hotkey with the highest weight from the most recent weights snapshot.
        Returns the miner hotkey if found, None if no weights are available.
        Uses async SQLAlchemy for better maintainability.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT miner_weights
                    FROM weights_history 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """))
                row = result.fetchone()
                
                if not row or not row[0]:
                    logger.warning("No weights found in database")
                    return None
                
                # miner_weights is already a dict, find the miner with highest weight
                miner_weights = json.loads(row[0])
                if not miner_weights:
                    logger.warning("Empty weights found in the latest snapshot")
                    return None
                
                # Find the miner with the highest weight
                top_miner_hotkey = max(miner_weights.items(), key=lambda x: float(x[1]))[0]
                top_weight = miner_weights[top_miner_hotkey]
                
                logger.info(f"Current top miner: {top_miner_hotkey} with weight {top_weight}")
                return top_miner_hotkey
                    
            except Exception as e:
                logger.error(f"Error getting current top miner: {str(e)}")
                return None
        
    async def get_weights_history_last_24h_with_prior(self) -> List[WeightsData]:
        """
        Returns all rows from weights_history with timestamp >= NOW() - INTERVAL '24 hours',
        plus the single row that immediately precedes that window (for continuity).
        Returns a list of WeightsData models.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    (
                        SELECT id, timestamp, time_since_last_update, miner_weights
                        FROM weights_history
                        WHERE timestamp < NOW() - INTERVAL '24 hours'
                        ORDER BY timestamp DESC
                        LIMIT 1
                    )
                    UNION ALL
                    (
                        SELECT id, timestamp, time_since_last_update, miner_weights
                        FROM weights_history
                        WHERE timestamp >= NOW() - INTERVAL '24 hours'
                        ORDER BY timestamp ASC
                    )
                    ORDER BY timestamp ASC
                """))
                rows = result.fetchall()
                return [WeightsData(
                    id=str(row[0]),
                    timestamp=row[1],
                    time_since_last_update=row[2],
                    miner_weights=row[3]
                ) for row in rows]
            except Exception as e:
                logger.error(f"Error fetching weights_history for last 24h with prior: {str(e)}")
                return []
        
    async def get_queue_info(self, version_id: str) -> List[QueueInfo]:
        """
        For a given version_id, for each evaluation:
        - If running, place_in_queue=0
        - If waiting, place_in_queue=(number of waiting for that validator with earlier created_at)+1
        - If completed, place_in_queue=-1
        - If replaced, place_in_queue=None
        Returns a list of QueueInfo(validator_hotkey, place_in_queue) for each evaluation for the version.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT validator_hotkey, created_at, status
                    FROM evaluations
                    WHERE version_id = :version_id
                    ORDER BY created_at ASC
                """), {'version_id': version_id})
                evals = result.fetchall()
                if not evals:
                    return []

                queue_info_list = []
                for validator_hotkey, created_at, status in evals:
                    if status == "running":
                        place_in_queue = 0
                    elif status == "waiting":
                        result = await session.execute(text("""
                            SELECT COUNT(*)
                            FROM evaluations
                            WHERE status = 'waiting' AND validator_hotkey = :validator_hotkey AND created_at < :created_at
                        """), {'validator_hotkey': validator_hotkey, 'created_at': created_at})
                        place_in_queue = result.fetchone()[0] + 1
                    elif status == "completed":
                        place_in_queue = -1
                    elif status == "replaced":
                        place_in_queue = None
                    else:
                        place_in_queue = None
                    queue_info_list.append(QueueInfo(
                        validator_hotkey=validator_hotkey,
                        place_in_queue=place_in_queue
                    ))
                logger.info(f"Found queue info for {len(queue_info_list)} evaluations for version {version_id}")
                return queue_info_list
            except Exception as e:
                logger.error(f"Error getting queue info: {str(e)}")
                return []
        
    async def create_evaluations_for_validator(self, validator_hotkey: str) -> int:
        """
        Create evaluations for agent versions created in the last 24 hours that are the latest version for their agent.
        Only creates evaluations for versions that don't already have an evaluation for this validator.
        Returns the number of evaluations created.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT av.version_id, av.agent_id, av.version_num, av.created_at
                    FROM agent_versions av
                    JOIN agents a ON av.agent_id = a.agent_id
                    WHERE av.created_at >= NOW() - INTERVAL '24 hours'
                    AND av.version_num = a.latest_version
                    ORDER BY av.created_at DESC
                """))
                
                agent_versions = result.fetchall()
                if not agent_versions:
                    logger.info(f"No recent agent versions found for validator {validator_hotkey}")
                    return 0
                
                evaluations_created = 0
                
                for version_row in agent_versions:
                    version_id, agent_id, version_num, created_at = version_row
                    
                    existing_result = await session.execute(text("""
                        SELECT evaluation_id 
                        FROM evaluations 
                        WHERE version_id = :version_id AND validator_hotkey = :validator_hotkey
                    """), {'version_id': version_id, 'validator_hotkey': validator_hotkey})
                    
                    if existing_result.fetchone():
                        logger.debug(f"Evaluation already exists for version {version_id} and validator {validator_hotkey}")
                        continue
                    
                    evaluation_id = str(uuid.uuid4())
                    stmt = insert(Evaluation).values(
                        evaluation_id=evaluation_id,
                        version_id=version_id,
                        validator_hotkey=validator_hotkey,
                        status='waiting',
                        created_at=datetime.now(),
                        started_at=None,
                        finished_at=None,
                        score=None,
                        terminated_reason=None
                    )
                    await session.execute(stmt)
                    evaluations_created += 1
                
                await session.commit()
                return evaluations_created
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating evaluations for validator {validator_hotkey}: {str(e)}")
                return 0
        
    async def ban_agent(self, agent_id: str) -> int:
        """
        Ban an agent by deleting it and all related data from the database.
        Deletes in order: evaluation_runs -> evaluations -> agent_versions -> agents
        to respect foreign key constraints.
        Returns the number of rows deleted (1 if successful, 0 if agent not found).
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # First, check if the agent exists
                result = await session.execute(text("SELECT agent_id FROM agents WHERE agent_id = :agent_id"), {'agent_id': agent_id})
                if not result.fetchone():
                    logger.warning(f"Agent {agent_id} not found for deletion")
                    return 0
                
                # Delete in order to respect foreign key constraints:
                # 1. Delete evaluation_runs for evaluations of this agent's versions
                result = await session.execute(text("""
                    DELETE FROM evaluation_runs 
                    WHERE evaluation_id IN (
                        SELECT e.evaluation_id 
                        FROM evaluations e
                        JOIN agent_versions av ON e.version_id = av.version_id
                        WHERE av.agent_id = :agent_id
                    )
                """), {'agent_id': agent_id})
                runs_deleted = result.rowcount
                
                # 2. Delete evaluations for this agent's versions
                result = await session.execute(text("""
                    DELETE FROM evaluations 
                    WHERE version_id IN (
                        SELECT version_id 
                        FROM agent_versions 
                        WHERE agent_id = :agent_id
                    )
                """), {'agent_id': agent_id})
                evaluations_deleted = result.rowcount
                
                # 3. Delete agent_versions for this agent
                result = await session.execute(text("DELETE FROM agent_versions WHERE agent_id = :agent_id"), {'agent_id': agent_id})
                versions_deleted = result.rowcount
                
                # 4. Finally delete the agent
                result = await session.execute(text("DELETE FROM agents WHERE agent_id = :agent_id"), {'agent_id': agent_id})
                agent_deleted = result.rowcount
                
                logger.info(f"Banned agent {agent_id}: deleted {runs_deleted} evaluation runs, {evaluations_deleted} evaluations, {versions_deleted} versions, {agent_deleted} agent")
                return agent_deleted
                
            except Exception as e:
                logger.error(f"Error banning agent: {str(e)}")
                return 0
        