import os
from typing import Optional, List
import json
import threading
import uuid
from dotenv import load_dotenv
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func, and_, text, Integer
from sqlalchemy.dialects.postgresql import insert

from api.src.utils.models import AgentSummary, Execution, AgentSummaryResponse, AgentDetailsNew, AgentVersionNew, ExecutionNew, AgentVersionDetails, QueueInfo, TopAgentHotkey, AgentVersionResponse, AgentResponse, EvaluationResponse, EvaluationRunResponse, RunningAgentEval, WeightsData, DashboardStats
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
                pool_size=20,
                max_overflow=40,
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_timeout=60,
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
                    AND table_name IN ('agents', 'agent_versions', 'evaluations', 'evaluation_runs', 'weights_history', 'banned_hotkeys', 'approved_version_ids', 'current_approved_leader', 'pending_approvals')
                """))
                existing_tables = [row[0] for row in result.fetchall()]
            
            logger.info(f"Existing database tables: {existing_tables}")

            required_tables = ['agent_versions', 'agents', 'evaluation_runs', 'evaluations', 'weights_history', 'banned_hotkeys', 'approved_version_ids', 'current_approved_leader', 'pending_approvals']
            missing_tables = [table for table in required_tables if table not in existing_tables]
            
            if missing_tables:
                logger.info(f"Creating missing tables: {missing_tables}")

            # Create tables using SQLAlchemy
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            
            # Execute the SQL schema file to create additional tables and indexes
            schema_file_path = os.path.join(os.path.dirname(__file__), 'postgres_schema.sql')
            if os.path.exists(schema_file_path):
                logger.info("Executing postgres_schema.sql to ensure all tables and indexes exist")
                with open(schema_file_path, 'r') as schema_file:
                    schema_sql = schema_file.read()
                
                # Use the engine directly to ensure proper transaction handling
                async with self.engine.begin() as conn:
                    # Split on semicolons and execute each statement separately
                    statements = [stmt.strip() for stmt in schema_sql.split(';') if stmt.strip()]
                    for statement in statements:
                        try:
                            await conn.execute(text(statement))
                        except Exception as e:
                            # Log but don't fail - some statements might already exist
                            logger.debug(f"SQL statement execution note (likely benign): {e}")
                    # Transaction is automatically committed when the context exits
                    logger.info("Successfully executed postgres_schema.sql")
            else:
                logger.warning(f"Schema file not found at {schema_file_path}")
            
            # Create our approval system tables directly (since schema parsing might have issues)
            # Check each approval table individually and create only the missing ones
            approval_tables_needed = {
                'approved_version_ids': 'approved_version_ids' in missing_tables,
                'current_approved_leader': 'current_approved_leader' in missing_tables,
                'pending_approvals': 'pending_approvals' in missing_tables
            }
            
            tables_to_create = [table for table, needed in approval_tables_needed.items() if needed]
            if tables_to_create:
                logger.info(f"Creating missing approval system tables: {tables_to_create}")
                async with self.engine.begin() as conn:
                    # Create approved_version_ids table if missing
                    if 'approved_version_ids' in tables_to_create:
                        await conn.execute(text("""
                            CREATE TABLE IF NOT EXISTS approved_version_ids (
                                version_id UUID PRIMARY KEY REFERENCES agent_versions(version_id),
                                approved_at TIMESTAMP NOT NULL DEFAULT NOW()
                            )
                        """))
                        logger.info("Created approved_version_ids table")
                    
                    # Create current_approved_leader table if missing
                    if 'current_approved_leader' in tables_to_create:
                        await conn.execute(text("""
                            CREATE TABLE IF NOT EXISTS current_approved_leader (
                                id INT PRIMARY KEY DEFAULT 1,
                                version_id UUID REFERENCES agent_versions(version_id),
                                score FLOAT,
                                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                                CONSTRAINT single_row CHECK (id = 1)
                            )
                        """))
                        logger.info("Created current_approved_leader table")
                    
                    # Create pending_approvals table if missing
                    if 'pending_approvals' in tables_to_create:
                        await conn.execute(text("""
                            CREATE TABLE IF NOT EXISTS pending_approvals (
                                version_id UUID PRIMARY KEY REFERENCES agent_versions(version_id),
                                agent_name TEXT NOT NULL,
                                miner_hotkey TEXT NOT NULL,
                                version_num INT NOT NULL,
                                score FLOAT NOT NULL,
                                detected_at TIMESTAMP NOT NULL DEFAULT NOW(),
                                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                                reviewed_at TIMESTAMP
                            )
                        """))
                        logger.info("Created pending_approvals table")
                    
                    # Create indexes for the tables that were created
                    if 'approved_version_ids' in tables_to_create:
                        await conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS idx_approved_version_ids_approved_at 
                            ON approved_version_ids(approved_at)
                        """))
                    
                    if 'pending_approvals' in tables_to_create:
                        await conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS idx_pending_approvals_status 
                            ON pending_approvals(status)
                        """))
                        await conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS idx_pending_approvals_detected_at 
                            ON pending_approvals(detected_at DESC)
                        """))
                        await conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS idx_pending_approvals_version_id 
                            ON pending_approvals(version_id)
                        """))
                    
                logger.info("Successfully created missing approval system tables")
            
            # Verify that the new tables were created by checking again
            async with self.AsyncSessionLocal() as session:
                result = await session.execute(text("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('approved_version_ids', 'current_approved_leader', 'pending_approvals')
                """))
                new_tables = [row[0] for row in result.fetchall()]
                logger.info(f"Verified new tables exist: {new_tables}")
            
            logger.info("Database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {str(e)}")
            raise

    async def close(self):
        """Close the database engine."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database engine closed")
    
    async def get_pool_status(self) -> dict:
        """Get connection pool status for monitoring."""
        if not self.engine:
            return {"error": "Engine not initialized"}
        
        pool = self.engine.pool
        try:
            return {
                "pool_size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "total_connections": pool.checkedin() + pool.checkedout(),
                "pool_type": type(pool).__name__
            }
        except AttributeError as e:
            # Fallback for async pools that don't have all methods
            return {
                "pool_size": getattr(pool, '_pool_size', 'unknown'),
                "checked_out": getattr(pool, '_checked_out', 'unknown'),
                "overflow": getattr(pool, '_overflow', 'unknown'),
                "pool_type": type(pool).__name__,
                "error": f"Some pool methods not available: {e}"
            }
        
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
        """Store an evaluation run and calculate scores"""
        async with self.AsyncSessionLocal() as session:
            try:
                stmt = insert(EvaluationRun).values(
                    run_id=evaluation_run.run_id,
                    evaluation_id=evaluation_run.evaluation_id,
                    swebench_instance_id=evaluation_run.swebench_instance_id,
                    response=evaluation_run.response,
                    error=evaluation_run.error,
                    pass_to_fail_success=evaluation_run.pass_to_fail_success,
                    fail_to_pass_success=evaluation_run.fail_to_pass_success,
                    pass_to_pass_success=evaluation_run.pass_to_pass_success,
                    fail_to_fail_success=evaluation_run.fail_to_fail_success,
                    solved=evaluation_run.solved,
                    status=evaluation_run.status,
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
                logger.error(f"Error storing evaluation run: {str(e)}")
                return 0

    async def _check_for_new_high_score(self, evaluation_id: str):
        """Check if a new evaluation creates a high score that beats current approved leader"""
        async with self.AsyncSessionLocal() as session:
            try:
                # Get the version_id and new score for this evaluation
                result = await session.execute(text("""
                    SELECT e.version_id, e.score, av.agent_id
                    FROM evaluations e
                    JOIN agent_versions av ON e.version_id = av.version_id  
                    WHERE e.evaluation_id = :evaluation_id
                    AND e.score IS NOT NULL
                """), {'evaluation_id': evaluation_id})
                
                row = result.fetchone()
                if not row:
                    return  # No score available yet
                
                version_id, new_score, agent_id = row
                
                # Convert UUID objects to strings
                version_id = str(version_id)
                agent_id = str(agent_id)
                
                # Get current approved leader info
                current_leader = await self.get_current_approved_leader()
                current_leader_score = current_leader['score'] if current_leader else 0.0
                
                # Only proceed if this is a new high score
                if new_score <= current_leader_score:
                    return
                
                # Get agent details for notification
                agent_result = await session.execute(text("""
                    SELECT a.name, a.miner_hotkey, av.version_num
                    FROM agents a
                    JOIN agent_versions av ON a.agent_id = av.agent_id
                    WHERE av.version_id = :version_id
                """), {'version_id': version_id})
                
                agent_row = agent_result.fetchone()
                if not agent_row:
                    return
                    
                agent_name, miner_hotkey, version_num = agent_row
                
                # Add to pending approvals queue for manual review
                try:
                    pending_result = await self.add_pending_approval(
                        version_id=version_id,
                        agent_name=agent_name,
                        miner_hotkey=miner_hotkey,
                        version_num=version_num,
                        score=new_score
                    )
                    
                    if pending_result == 1:
                        logger.info(f"Added version {version_id} to pending approvals queue (score: {new_score})")
                    else:
                        logger.warning(f"Failed to add version {version_id} to pending approvals queue")
                        
                except Exception as e:
                    logger.error(f"Error adding version {version_id} to pending approvals: {str(e)}")

                # Send Slack notification about new high score
                try:
                    from api.src.utils.slack import send_high_score_notification
                    
                    await send_high_score_notification(
                        agent_name=agent_name,
                        miner_hotkey=miner_hotkey, 
                        version_id=version_id,
                        version_num=version_num,
                        new_score=new_score,
                        previous_score=current_leader_score
                    )
                    logger.info(f"Sent high score notification for version {version_id} with score {new_score}")
                    
                except Exception as e:
                    logger.error(f"Failed to send Slack notification for high score: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Error checking for new high score: {str(e)}")
        
    async def get_next_evaluation(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the next evaluation for a validator. Return None if not found.
        Excludes evaluations for banned miner hotkeys.
        """
        # For now, this is a manual process, but will be updated shortly to be automatic
        banned_hotkeys = [
            "5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9", 
            "5Dyghsz26XyzWQ4mqrngfGeidM2FUp28Hpzp2Q4sAW21mFkx", 
            "5G6c6QvYnVS5nefgqoyq1rvKCzd3gzCQv2n6dmfLKetyVrwh", 
            "5EXLAJe1tZhaQWtsqp2DKdKpZ2oQ3WBYapUSXFCe9zgvy8Uk", 
            "5E7s2xNMzXKnurpsifmWxojcFa44NkuLPn1U7s9zvVrFjYKb",
            "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
            "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
            "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
            "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
            "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
            "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
            "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
            "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
            "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
            "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
            "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
            "5EtFbtr7wW4cAWCHcD1SLMBnExoAeKPg2eEDnzh8amjD8aPT",
            "5DhUkZwxYygbLwHdzPnZT2QAhKE3E5fkeey6pcYRhmBPhs9G",
            "5EcenAqMksipQZJ4x6xe9YbeXLCfMqba5z6URC1K4x1ZP9mT",
            "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
            "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
            "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
            "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
            "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
            "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
            "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
            "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
            "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
            "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
            "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
            "5GsKVvwQ4gk9QDk6bb8qSycPJoUcACGmfrQNjxRCE2Ue9wd3",
            "5DS6D4MEMoPC8VSpgDWq3rVqFkjNiQoj5toVCSLA1gak7GZL",
            "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
            "5Df5ukRD5fnDQZcMMqBkDaQ7dPhQXsfFrkD3s477eFqgPYZh",
            "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
            "5GP2KhQHKbzcWaW61buSTHtQyEJvcNB9qx9WfiRbtPzs3WfG",
            "5CB7jfYUT2Z4tXFWUmKbGqj3gdTRxXFPg7cATxvCYYn7tBt9",
            "5E812wJwEpcd12FcYhdv21CoMPotpmXsmbQgepmFs5jDLCzv",
            "5E4VUFyLbSgTGmy6Kd5eAb3UFabTX3VuT9M2CY2my8Dv2oRx",
            "5H3frA14nyVf5J1YYvS3qcKc1fwg2kbz997nE9uNyYLcEXSA",
            "5CDqtDptJ1JuAh1hX7pVoPMUXTVjdik2fb1RhybbxgjyUG9Y",
            "5EWjt1HQWxYsFnygJWcZug29KXB9RNTDoCJyZHKThtQSvVvi",
            "5H97WhmqAKj9vsE1K6PFLZTm48zAqTagrjuL59nZwqDu3cZK",
            "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
            "5DHYBuwoHoVDwL8awnhhJg2oCX5xqLzsoU9ZMEg3g1gdtkXc",
            "5FLQp1rxNiDiCA2Bwna5AUu1eScJf6hT1JtfEMfFeWio1MYz",
            "5HKzmZ1h9CGmyBxAfESBgMPXJKThTMxCB4vwBn8CEVq7GvgG",
            "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
            "5EqVMYTTxnuqbxzJ19Y7tcu2CbmjG3unuZmreB6c1ksa4q1f",
            "5FX1koxncfZpeDqB8fFQhae52wX4z8q8hEHceb46W6upscc4",
            "5HmwgHVLSvmVxqA2BQ7z9xyedCTdqo5CXHGFjYAqGGEzyHux",
            "5HBLnpw5yqPzcnpYo4NfMbutH3B3W1sqsegwtah2NBPPU2TT",
            "5FbyeY3nGuYdb4FhXtYEDhVqdFJKopko2P3tTbNZWWh1T3XW",
            "5GWoFBuh3cgaA2GDq1urNh6KsKEzu9mhoGvmhnaNikeS7YYs",
            "5HBAY8VVBcdEuU7EWvtdTN9hrDKXcQneea3mL7YzqHjBgs8Q",
            "5E4DVZUBpCWXmBTgZ6wqbqrSEHgUCQRsXSALW6NGFGMjYndv",
            "5Epn64DZ3oPqdpZKroGa9ArFVCBHDWNdrD7aWcMJk4eA6kbM",
            "5FeYEJXzEHwW9jW4igyse9W4PJBab2eWyCN86V2yV5VhYd8Q",
            "5CAeKnKfcxD3CyY7st8yfk6a46DDvruLZECQofbUsFigsi7b",
            "5GjjR4rCctSC14uFB5bZkEq6S4quQnexvGf1RoiLg4Zb2FXE",
            "5EFXBbybeDQGqGLNuf4cizuqtE4DTzH8yUV6Nh6GhAdNicra",
            "5CSa9KZUVUCkcWeWRMykK98V4TUTXBHAtuSqZoi2AHvB4w1P",
            "5F1QRLR65LYGYiVHW43gVxmvBkuicXSJy9TiDXvxYXS8dZG9",
            "5FQzhHZGhCgUGZSt9afRhbtXMX16WSvG6vFMHK1YwhnzrmrT",
            "5Da6Yej8xxY7ehH5xpB34FDWZreFn9ktndGZg6FbmXvJzXAZ",
            "5E5JhsZ4jocJy4awXbSEsm92RYBKRriirSXys7iaSBpkoHvC",
            "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV",
            "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV"
        ]

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
        Gets the top approved agents miner hotkey and version id from the database,
        where its been scored by at least 1 validator and is in the approved versions list.
        Excludes banned miner hotkeys from consideration.
        Returns None if no approved versions exist.
        """
        # For now, this is a manual process, but will be updated shortly to be automatic
        banned_hotkeys = [
            "5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9", 
            "5Dyghsz26XyzWQ4mqrngfGeidM2FUp28Hpzp2Q4sAW21mFkx", 
            "5G6c6QvYnVS5nefgqoyq1rvKCzd3gzCQv2n6dmfLKetyVrwh", 
            "5EXLAJe1tZhaQWtsqp2DKdKpZ2oQ3WBYapUSXFCe9zgvy8Uk", 
            "5E7s2xNMzXKnurpsifmWxojcFa44NkuLPn1U7s9zvVrFjYKb",
            "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
            "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
            "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
            "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
            "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
            "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
            "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
            "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
            "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
            "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
            "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
            "5EtFbtr7wW4cAWCHcD1SLMBnExoAeKPg2eEDnzh8amjD8aPT",
            "5DhUkZwxYygbLwHdzPnZT2QAhKE3E5fkeey6pcYRhmBPhs9G",
            "5EcenAqMksipQZJ4x6xe9YbeXLCfMqba5z6URC1K4x1ZP9mT",
            "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
            "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
            "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
            "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
            "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
            "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
            "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
            "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
            "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
            "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
            "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
            "5GsKVvwQ4gk9QDk6bb8qSycPJoUcACGmfrQNjxRCE2Ue9wd3",
            "5DS6D4MEMoPC8VSpgDWq3rVqFkjNiQoj5toVCSLA1gak7GZL",
            "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
            "5Df5ukRD5fnDQZcMMqBkDaQ7dPhQXsfFrkD3s477eFqgPYZh",
            "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
            "5GP2KhQHKbzcWaW61buSTHtQyEJvcNB9qx9WfiRbtPzs3WfG",
            "5CB7jfYUT2Z4tXFWUmKbGqj3gdTRxXFPg7cATxvCYYn7tBt9",
            "5E812wJwEpcd12FcYhdv21CoMPotpmXsmbQgepmFs5jDLCzv",
            "5E4VUFyLbSgTGmy6Kd5eAb3UFabTX3VuT9M2CY2my8Dv2oRx",
            "5H3frA14nyVf5J1YYvS3qcKc1fwg2kbz997nE9uNyYLcEXSA",
            "5CDqtDptJ1JuAh1hX7pVoPMUXTVjdik2fb1RhybbxgjyUG9Y",
            "5EWjt1HQWxYsFnygJWcZug29KXB9RNTDoCJyZHKThtQSvVvi",
            "5H97WhmqAKj9vsE1K6PFLZTm48zAqTagrjuL59nZwqDu3cZK",
            "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
            "5DHYBuwoHoVDwL8awnhhJg2oCX5xqLzsoU9ZMEg3g1gdtkXc",
            "5FLQp1rxNiDiCA2Bwna5AUu1eScJf6hT1JtfEMfFeWio1MYz",
            "5HKzmZ1h9CGmyBxAfESBgMPXJKThTMxCB4vwBn8CEVq7GvgG",
            "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
            "5EqVMYTTxnuqbxzJ19Y7tcu2CbmjG3unuZmreB6c1ksa4q1f",
            "5FX1koxncfZpeDqB8fFQhae52wX4z8q8hEHceb46W6upscc4",
            "5HmwgHVLSvmVxqA2BQ7z9xyedCTdqo5CXHGFjYAqGGEzyHux",
            "5HBLnpw5yqPzcnpYo4NfMbutH3B3W1sqsegwtah2NBPPU2TT",
            "5FbyeY3nGuYdb4FhXtYEDhVqdFJKopko2P3tTbNZWWh1T3XW",
            "5GWoFBuh3cgaA2GDq1urNh6KsKEzu9mhoGvmhnaNikeS7YYs",
            "5HBAY8VVBcdEuU7EWvtdTN9hrDKXcQneea3mL7YzqHjBgs8Q",
            "5E4DVZUBpCWXmBTgZ6wqbqrSEHgUCQRsXSALW6NGFGMjYndv",
            "5Epn64DZ3oPqdpZKroGa9ArFVCBHDWNdrD7aWcMJk4eA6kbM",
            "5FeYEJXzEHwW9jW4igyse9W4PJBab2eWyCN86V2yV5VhYd8Q",
            "5CAeKnKfcxD3CyY7st8yfk6a46DDvruLZECQofbUsFigsi7b",
            "5GjjR4rCctSC14uFB5bZkEq6S4quQnexvGf1RoiLg4Zb2FXE",
            "5EFXBbybeDQGqGLNuf4cizuqtE4DTzH8yUV6Nh6GhAdNicra",
            "5CSa9KZUVUCkcWeWRMykK98V4TUTXBHAtuSqZoi2AHvB4w1P",
            "5F1QRLR65LYGYiVHW43gVxmvBkuicXSJy9TiDXvxYXS8dZG9",
            "5FQzhHZGhCgUGZSt9afRhbtXMX16WSvG6vFMHK1YwhnzrmrT",
            "5Da6Yej8xxY7ehH5xpB34FDWZreFn9ktndGZg6FbmXvJzXAZ",
            "5E5JhsZ4jocJy4awXbSEsm92RYBKRriirSXys7iaSBpkoHvC",
            "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV",
            "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV"
        ]

        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    WITH approved_version_scores AS (               -- 1.  score + validator count for APPROVED versions only
                        SELECT
                            e.version_id,
                            AVG(e.score)                       AS avg_score,
                            COUNT(DISTINCT e.validator_hotkey) AS validator_cnt
                        FROM evaluations e
                        JOIN approved_version_ids av_approved ON e.version_id = av_approved.version_id  -- ONLY approved versions
                        WHERE e.status = 'completed'
                        AND e.score  IS NOT NULL
                        GROUP BY e.version_id
                        HAVING COUNT(DISTINCT e.validator_hotkey) >= 1
                    ),

                    top_approved_score AS (                         -- 2.  the absolute best score among approved versions
                        SELECT MAX(avg_score) AS max_score
                        FROM approved_version_scores
                    ),

                    close_enough_approved AS (                      -- 3.  approved scores ≥ 98% of the best approved
                        SELECT
                            avs.version_id,
                            avs.avg_score,
                            av.created_at,
                            ROW_NUMBER() OVER (ORDER BY av.created_at ASC) AS rn  -- oldest first
                        FROM approved_version_scores avs
                        JOIN agent_versions av ON av.version_id = avs.version_id
                        CROSS JOIN top_approved_score tas
                        WHERE avs.avg_score >= tas.max_score * 0.98    -- within 2%
                    )

                    SELECT
                        a.miner_hotkey,
                        cea.version_id,
                        cea.avg_score
                    FROM close_enough_approved cea
                    JOIN agent_versions av ON av.version_id = cea.version_id
                    JOIN agents         a  ON a.agent_id    = av.agent_id
                    WHERE cea.rn = 1
                    AND a.miner_hotkey != ALL(:banned_hotkeys);
                    """), {'banned_hotkeys': banned_hotkeys})

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
                logger.info(f"Found {len(evals)} evaluations for version {version_id}")
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
                
                await session.commit()
                return agent_deleted
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error banning agent: {str(e)}")
                return 0
        
    async def clean_hanging_evaluations(self) -> int:
        """
        Clean up evaluations that are stuck in 'running' status.
        For each running evaluation:
        1. Set status to 'waiting'
        2. Set nullable fields to null (started_at, finished_at, score, terminated_reason)
        3. Delete all associated evaluation runs
        Returns the number of evaluations cleaned up.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # Single query to clean up hanging evaluations and get count
                result = await session.execute(text("""
                    WITH cleaned_evaluations AS (
                        UPDATE evaluations 
                        SET status = 'waiting',
                            started_at = NULL,
                            finished_at = NULL,
                            score = NULL,
                            terminated_reason = NULL
                        WHERE status = 'running'
                        RETURNING evaluation_id
                    ),
                    deleted_runs AS (
                        DELETE FROM evaluation_runs 
                        WHERE evaluation_id IN (SELECT evaluation_id FROM cleaned_evaluations)
                    )
                    SELECT COUNT(*) as cleaned_count
                    FROM cleaned_evaluations
                """))
                
                cleaned_count = result.fetchone()[0]
                
                if cleaned_count > 0:
                    logger.info(f"Successfully cleaned up {cleaned_count} running evaluations")
                else:
                    logger.info("Tried to clean up running evaluations, but no running evaluations found")
                
                await session.commit()
                return cleaned_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up running evaluations: {str(e)}")
                return 0

    async def clean_timed_out_evaluations(self) -> int:
        """
        Clean up evaluations that have been running for more than 150 minutes.
        For each timed out evaluation:
        1. Set status to 'waiting'
        2. Set nullable fields to null (started_at, finished_at, score, terminated_reason)
        3. Delete all associated evaluation runs
        Returns the number of evaluations cleaned up.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT evaluation_id 
                    FROM evaluations 
                    WHERE status = 'running' 
                    AND started_at < NOW() - INTERVAL '150 minutes'
                """))
                
                timed_out_evaluations = result.fetchall()
                if not timed_out_evaluations:
                    logger.info("Tried to clean up timed out evaluations, but no evaluations have been running for over 150 minutes")
                    return 0
                
                cleaned_count = 0
                
                for (evaluation_id,) in timed_out_evaluations:
                    result = await session.execute(text("""
                        DELETE FROM evaluation_runs 
                        WHERE evaluation_id = :evaluation_id
                    """), {'evaluation_id': evaluation_id})
                    
                    runs_deleted = result.rowcount
                    
                    result = await session.execute(text("""
                        UPDATE evaluations 
                        SET status = 'waiting',
                            started_at = NULL,
                            finished_at = NULL,
                            score = NULL,
                            terminated_reason = NULL
                        WHERE evaluation_id = :evaluation_id
                    """), {'evaluation_id': evaluation_id})
                    
                    if result.rowcount > 0:
                        cleaned_count += 1
                        logger.info(f"Cleaned up timed out evaluation {evaluation_id}: deleted {runs_deleted} associated runs, reset to waiting")
                
                await session.commit()
                logger.info(f"Successfully cleaned up {cleaned_count} timed out evaluations")
                return cleaned_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up timed out evaluations: {str(e)}")
                return 0

    async def get_runs_for_evaluation(self, evaluation_id: str) -> list[EvaluationRunResponse]:
        """
        Get all runs for a given evaluation.
        Returns a list of EvaluationRunResponse objects.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT run_id, evaluation_id, swebench_instance_id, status, response, error, pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, fail_to_fail_success, solved, started_at, sandbox_created_at, patch_generated_at, eval_started_at, result_scored_at
                    FROM evaluation_runs
                    WHERE evaluation_id = :evaluation_id
                """), {'evaluation_id': evaluation_id})
                runs = result.fetchall()
                return [EvaluationRunResponse(
                    run_id=str(row[0]),
                    evaluation_id=str(row[1]),
                    swebench_instance_id=str(row[2]),
                    status=row[3],
                    response=row[4],
                    error=row[5],
                    pass_to_fail_success=row[6],
                    fail_to_pass_success=row[7],
                    pass_to_pass_success=row[8],
                    fail_to_fail_success=row[9],
                    solved=row[10],
                    started_at=row[11],
                    sandbox_created_at=row[12],
                    patch_generated_at=row[13],
                    eval_started_at=row[14],
                    result_scored_at=row[15]
                ) for row in runs]
            except Exception as e:
                logger.error(f"Error retrieving runs for evaluation {evaluation_id}: {e}")
                return []

    async def get_dashboard_statistics(self) -> DashboardStats:
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT
                        COUNT(*) as number_of_agents,
                        COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as agent_iterations_last_24_hours,
                        MAX(score) as top_agent_score,
                        MAX(score) - COALESCE(MAX(CASE WHEN created_at <= NOW() - INTERVAL '24 hours' THEN score END), 0) as daily_score_improvement
                    FROM agent_versions;
                    """))
                
                statistics = result.fetchone()

                return DashboardStats(
                    number_of_agents=statistics[0],
                    agent_iterations_last_24_hours=statistics[1],
                    top_agent_score=statistics[2],
                    daily_score_improvement=statistics[3]
                )
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error fetching daily dashboard stats: {str(e)}")
                return 0
    
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

    # ========================================
    # APPROVAL SYSTEM METHODS
    # ========================================

    async def approve_version_id(self, version_id: str) -> int:
        """
        Approve a version ID for weight consideration.
        Returns 1 if successful, 0 if failed.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # Check if version exists
                version_result = await session.execute(text("""
                    SELECT version_id FROM agent_versions WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                if not version_result.fetchone():
                    logger.error(f"Version {version_id} not found in agent_versions")
                    return 0

                # Insert into approved_version_ids (on conflict do nothing)
                await session.execute(text("""
                    INSERT INTO approved_version_ids (version_id)
                    VALUES (:version_id)
                    ON CONFLICT (version_id) DO NOTHING
                """), {
                    'version_id': version_id
                })
                
                await session.commit()
                logger.info(f"Version {version_id} approved for weight consideration")
                return 1
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error approving version {version_id}: {str(e)}")
                return 0

    async def is_version_approved(self, version_id: str) -> bool:
        """
        Check if a version ID is approved for weight consideration.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 1 FROM approved_version_ids WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                return result.fetchone() is not None
                
            except Exception as e:
                logger.error(f"Error checking if version {version_id} is approved: {str(e)}")
                return False

    async def get_current_approved_leader(self) -> Optional[dict]:
        """
        Get the current approved leader information.
        Returns dict with version_id and score, or None if no leader set.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT version_id, score, updated_at 
                    FROM current_approved_leader 
                    WHERE id = 1
                """))
                
                row = result.fetchone()
                if row and row[0]:  # Check if version_id is not None
                    return {
                        'version_id': str(row[0]),
                        'score': row[1],
                        'updated_at': row[2]
                    }
                return None
                
            except Exception as e:
                logger.error(f"Error getting current approved leader: {str(e)}")
                return None

    async def update_approved_leader_if_better(self, version_id: str) -> bool:
        """
        Update the current approved leader if the given version has a better score.
        Returns True if leader was updated, False otherwise.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # Get the score for the given version
                version_result = await session.execute(text("""
                    SELECT score FROM agent_versions WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                version_row = version_result.fetchone()
                if not version_row or version_row[0] is None:
                    logger.warning(f"Version {version_id} not found or has no score")
                    return False
                
                new_score = version_row[0]
                
                # Get current leader
                current_leader = await self.get_current_approved_leader()
                
                # Update if no current leader or new score is better
                should_update = (current_leader is None or 
                               current_leader['score'] is None or 
                               new_score > current_leader['score'])
                
                if should_update:
                    # Upsert the new leader
                    await session.execute(text("""
                        INSERT INTO current_approved_leader (id, version_id, score, updated_at)
                        VALUES (1, :version_id, :score, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            version_id = EXCLUDED.version_id,
                            score = EXCLUDED.score,
                            updated_at = EXCLUDED.updated_at
                    """), {
                        'version_id': version_id,
                        'score': new_score
                    })
                    
                    await session.commit()
                    logger.info(f"Updated approved leader to version {version_id} with score {new_score}")
                    return True
                else:
                    logger.info(f"Version {version_id} score {new_score} not better than current leader score {current_leader['score']}")
                    return False
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating approved leader for version {version_id}: {str(e)}")
                return False

    async def get_approved_versions(self, limit: int = 100) -> List[dict]:
        """
        Get list of all approved versions for debugging/admin purposes.
        Returns list of dicts with version_id, approved_at.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 
                        av.version_id,
                        av.approved_at,
                        agv.score,
                        a.miner_hotkey,
                        a.name
                    FROM approved_version_ids av
                    JOIN agent_versions agv ON av.version_id = agv.version_id
                    JOIN agents a ON agv.agent_id = a.agent_id
                    ORDER BY av.approved_at DESC
                    LIMIT :limit
                """), {'limit': limit})
                
                return [{
                    'version_id': str(row[0]),
                    'approved_at': row[1],
                    'score': row[2],
                    'miner_hotkey': row[3],
                    'agent_name': row[4]
                } for row in result.fetchall()]
                
            except Exception as e:
                logger.error(f"Error getting approved versions: {str(e)}")
                return []

    async def remove_version_approval(self, version_id: str) -> int:
        """
        Remove approval for a version ID (admin function).
        Returns 1 if successful, 0 if failed.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    DELETE FROM approved_version_ids WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                deleted_count = result.rowcount
                
                # If we removed the current leader, clear the leader
                current_leader = await self.get_current_approved_leader()
                if current_leader and current_leader['version_id'] == version_id:
                    await session.execute(text("""
                        UPDATE current_approved_leader 
                        SET version_id = NULL, score = NULL, updated_at = NOW()
                        WHERE id = 1
                    """))
                    logger.info(f"Cleared current approved leader since version {version_id} approval was removed")
                
                await session.commit()
                logger.info(f"Removed approval for version {version_id}")
                return deleted_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error removing approval for version {version_id}: {str(e)}")
                return 0

    # ========================================
    # PENDING APPROVALS METHODS
    # ========================================

    async def add_pending_approval(self, version_id: str, agent_name: str, miner_hotkey: str, version_num: int, score: float) -> int:
        """
        Add a high-scoring agent version to the pending approvals queue.
        Returns 1 if successful, 0 if failed (including if already exists).
        """
        async with self.AsyncSessionLocal() as session:
            try:
                # Check if version exists
                version_result = await session.execute(text("""
                    SELECT version_id FROM agent_versions WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                if not version_result.fetchone():
                    logger.error(f"Version {version_id} not found in agent_versions")
                    return 0

                # Insert into pending_approvals (on conflict do nothing due to PRIMARY KEY constraint)
                await session.execute(text("""
                    INSERT INTO pending_approvals (version_id, agent_name, miner_hotkey, version_num, score)
                    VALUES (:version_id, :agent_name, :miner_hotkey, :version_num, :score)
                    ON CONFLICT (version_id) DO NOTHING
                """), {
                    'version_id': version_id,
                    'agent_name': agent_name,
                    'miner_hotkey': miner_hotkey,
                    'version_num': version_num,
                    'score': score
                })
                
                await session.commit()
                logger.info(f"Added pending approval for version {version_id} (agent: {agent_name}, score: {score})")
                return 1
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding pending approval for version {version_id}: {str(e)}")
                return 0

    async def get_pending_approvals(self, status: str = 'pending', limit: int = 50) -> List[dict]:
        """
        Get list of pending approvals filtered by status.
        Returns list of dicts with pending approval details.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 
                        version_id,
                        agent_name,
                        miner_hotkey,
                        version_num,
                        score,
                        detected_at,
                        status,
                        reviewed_at
                    FROM pending_approvals
                    WHERE status = :status
                    ORDER BY detected_at DESC
                    LIMIT :limit
                """), {'status': status, 'limit': limit})
                
                return [{
                    'version_id': str(row[0]),
                    'agent_name': row[1],
                    'miner_hotkey': row[2],
                    'version_num': row[3],
                    'score': row[4],
                    'detected_at': row[5],
                    'status': row[6],
                    'reviewed_at': row[7]
                } for row in result.fetchall()]
                
            except Exception as e:
                logger.error(f"Error getting pending approvals: {str(e)}")
                return []

    async def update_pending_approval_status(self, version_id: str, status: str) -> int:
        """
        Update the status of a pending approval.
        Returns 1 if successful, 0 if failed.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    UPDATE pending_approvals 
                    SET status = :status, reviewed_at = NOW()
                    WHERE version_id = :version_id
                """), {
                    'version_id': version_id,
                    'status': status
                })
                
                updated_count = result.rowcount
                await session.commit()
                
                if updated_count > 0:
                    logger.info(f"Updated pending approval status for version {version_id} to {status}")
                else:
                    logger.warning(f"No pending approval found for version {version_id}")
                
                return updated_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating pending approval status for version {version_id}: {str(e)}")
                return 0

    async def get_pending_approval_by_version(self, version_id: str) -> Optional[dict]:
        """
        Get pending approval info for a specific version ID.
        Returns dict with pending approval details, or None if not found.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    SELECT 
                        version_id,
                        agent_name,
                        miner_hotkey,
                        version_num,
                        score,
                        detected_at,
                        status,
                        reviewed_at
                    FROM pending_approvals
                    WHERE version_id = :version_id
                """), {'version_id': version_id})
                
                row = result.fetchone()
                if row:
                    return {
                        'version_id': str(row[0]),
                        'agent_name': row[1],
                        'miner_hotkey': row[2],
                        'version_num': row[3],
                        'score': row[4],
                        'detected_at': row[5],
                        'status': row[6],
                        'reviewed_at': row[7]
                    }
                return None
                
            except Exception as e:
                logger.error(f"Error getting pending approval for version {version_id}: {str(e)}")
                return None

    async def cleanup_old_pending_approvals(self, days: int = 7) -> int:
        """
        Clean up old pending approvals (older than specified days).
        Returns number of records cleaned up.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(text("""
                    DELETE FROM pending_approvals 
                    WHERE detected_at < NOW() - INTERVAL '%s days'
                """ % days))
                
                deleted_count = result.rowcount
                await session.commit()
                
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old pending approvals (older than {days} days)")
                else:
                    logger.info(f"No old pending approvals found to clean up (older than {days} days)")
                
                return deleted_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error cleaning up old pending approvals: {str(e)}")
                return 0