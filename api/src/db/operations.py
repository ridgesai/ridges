import os
from typing import Optional, List
import psycopg2
from psycopg2 import pool
import json
import threading
import uuid
from dotenv import load_dotenv
from datetime import datetime
from api.src.utils.models import Agent, AgentVersion, EvaluationRun, Evaluation, AgentSummary, Execution, AgentSummaryResponse, AgentDetailsNew, AgentVersionNew, ExecutionNew, AgentVersionDetails, WeightsData, QueueInfo, TopAgentHotkey
from api.src.utils.logging_utils import get_logger
from .sqlalchemy_manager import SQLAlchemyDatabaseManager

load_dotenv()

logger = get_logger(__name__)

class DatabaseManager:
    _instance = None
    _lock = threading.Lock()
    _pool = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._initialize_pool()
                    self.init_tables()
                    # Initialize SQLAlchemy manager for modern operations
                    self.sqlalchemy_manager = SQLAlchemyDatabaseManager()
                    self._initialized = True
    
    def _initialize_pool(self):
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=5,
                maxconn=50,
                host=os.getenv('AWS_RDS_PLATFORM_ENDPOINT'),
                user=os.getenv('AWS_MASTER_USERNAME'),
                password=os.getenv('AWS_MASTER_PASSWORD'),
                database=os.getenv('AWS_RDS_PLATFORM_DB_NAME'),
                sslmode='require',
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5
            )
            logger.info("Database connection pool initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {str(e)}")
            raise
    
    def get_connection(self):
        """Get a connection from the pool"""
        if self._pool is None:
            raise Exception("Database pool not initialized")
        return self._pool.getconn()
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if self._pool is None:
            raise Exception("Database pool not initialized")
        self._pool.putconn(conn)
    
    def close_pool(self):
        """Close all connections in the pool"""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("Database connection pool closed")

    def init_tables(self):
        """
        Check if required tables exist and create them if they don't.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('agents', 'agent_versions', 'evaluations', 'evaluation_runs', 'weights_history', 'banned_hotkeys')
                """)
                existing_tables = [row[0] for row in cursor.fetchall()]
            
            logger.info(f"Existing database tables: {existing_tables}")

            required_tables = ['agent_versions', 'agents', 'evaluation_runs', 'evaluations', 'weights_history', 'banned_hotkeys']
            missing_tables = [table for table in required_tables if table not in existing_tables]
            
            if not missing_tables:
                logger.info("All required tables already exist")
                return
            
            logger.info(f"Creating missing tables: {missing_tables}")

            schema_path = os.path.join(os.path.dirname(__file__), 'postgres_schema.sql')
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            
            with conn.cursor() as cursor:
                cursor.execute(schema_sql)
            
            logger.info("Database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {str(e)}")
            raise
        finally:
            if conn:
                self.return_connection(conn)

    def close(self):
        """
        Close the database connection pool.
        """
        self.close_pool()
        
    def store_agent(self, agent: Agent) -> int:
        """
        Store an agent in the database. If the agent already exists, update latest_version and last_updated. Return 1 if successful, 0 if not.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.store_agent(agent)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for storing agent {agent.agent_id}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                conn.autocommit = True
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO agents (agent_id, miner_hotkey, name, latest_version, created_at, last_updated)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (agent_id) DO UPDATE SET
                            latest_version = EXCLUDED.latest_version,
                            last_updated = EXCLUDED.last_updated
                    """, (agent.agent_id, agent.miner_hotkey, agent.name, agent.latest_version, agent.created_at, agent.last_updated))
                    logger.info(f"Agent {agent.agent_id} stored successfully via fallback")
                    return 1
            except Exception as fallback_error:
                logger.error(f"Error storing agent {agent.agent_id}: {str(fallback_error)}")
                return 0
            finally:
                if conn:
                    self.return_connection(conn)
        
    def store_agent_version(self, agent_version: AgentVersion) -> int:
        """
        Store an agent version in the database. Return 1 if successful, 0 if not. If the agent version already exists, update the score.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.store_agent_version(agent_version)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for storing agent version {agent_version.version_id}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                conn.autocommit = True
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO agent_versions (version_id, agent_id, version_num, created_at, score)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (version_id) DO UPDATE SET
                            score = EXCLUDED.score
                        """, (agent_version.version_id, agent_version.agent_id, agent_version.version_num, agent_version.created_at, agent_version.score))
                    logger.info(f"Agent version {agent_version.version_id} stored successfully via fallback")
                    return 1
            except Exception as fallback_error:
                logger.error(f"Error storing agent version {agent_version.version_id}: {str(fallback_error)}")
                return 0
            finally:
                if conn:
                    self.return_connection(conn)
    
    def store_evaluation(self, evaluation: Evaluation) -> int:
        """
        Store an evaluation in the database. Return 1 if successful, 0 if not. If the evaluation already exists, update the status, started_at, and finished_at.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at, started_at, finished_at, terminated_reason, score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (evaluation_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        terminated_reason = EXCLUDED.terminated_reason,
                        score = EXCLUDED.score
                """, (evaluation.evaluation_id, evaluation.version_id, evaluation.validator_hotkey, evaluation.status, evaluation.created_at, evaluation.started_at, evaluation.finished_at, evaluation.terminated_reason, evaluation.score))
                logger.info(f"Evaluation {evaluation.evaluation_id} stored successfully")
                return 1
        except Exception as e:
            logger.error(f"Error storing evaluation {evaluation.evaluation_id}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)
        
    def update_agent_version_score(self, version_id: str) -> int:
        """
        Update the score for an agent version. Return 1 if successful, 0 if not.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:

                cursor.execute("""
                    SELECT AVG(score) as avg_score
                    FROM evaluations 
                    WHERE version_id = %s AND score IS NOT NULL
                """, (version_id,))
                result = cursor.fetchone()
                
                if result and result[0] is not None:
                    avg_score = float(result[0])
                    
                    cursor.execute("""
                        UPDATE agent_versions 
                        SET score = %s 
                        WHERE version_id = %s
                    """, (avg_score, version_id))
                    
                    logger.info(f"Updated agent version {version_id} with score {avg_score}")
                    return 1
                else:
                    logger.info(f"Tried to update agent version {version_id} with a score, but no scored evaluations found")
                    return 0
                    
        except Exception as e:
            logger.error(f"Error updating agent version score for {version_id}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)

    def store_evaluation_run(self, evaluation_run: EvaluationRun) -> int:
        """
        Store an evaluation run in the database. Return 1 if successful, 0 if not.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO evaluation_runs (
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
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        response = EXCLUDED.response,
                        error = EXCLUDED.error,
                        pass_to_fail_success = EXCLUDED.pass_to_fail_success,
                        fail_to_pass_success = EXCLUDED.fail_to_pass_success,
                        pass_to_pass_success = EXCLUDED.pass_to_pass_success,
                        fail_to_fail_success = EXCLUDED.fail_to_fail_success,
                        solved = EXCLUDED.solved,
                        sandbox_created_at = EXCLUDED.sandbox_created_at,
                        patch_generated_at = EXCLUDED.patch_generated_at,
                        eval_started_at = EXCLUDED.eval_started_at,
                        result_scored_at = EXCLUDED.result_scored_at
                """, (
                        evaluation_run.run_id,
                        evaluation_run.evaluation_id,
                        evaluation_run.swebench_instance_id,
                        evaluation_run.status,
                        evaluation_run.response,
                        evaluation_run.error,
                        evaluation_run.pass_to_fail_success,
                        evaluation_run.fail_to_pass_success,
                        evaluation_run.pass_to_pass_success,
                        evaluation_run.fail_to_fail_success,
                        evaluation_run.solved,
                        evaluation_run.started_at,
                        evaluation_run.sandbox_created_at,
                        evaluation_run.patch_generated_at,
                        evaluation_run.eval_started_at,
                        evaluation_run.result_scored_at
                    ))
                logger.info(f"Evaluation run {evaluation_run.run_id} stored successfully")

                # Update the score for the associated evaluation
                cursor.execute("""
                    UPDATE evaluations
                    SET score = (SELECT AVG(CASE WHEN solved THEN 1 ELSE 0 END)
                                FROM evaluation_runs
                                WHERE evaluation_id = %s)
                    WHERE evaluation_id = %s
                """, (evaluation_run.evaluation_id, evaluation_run.evaluation_id))
                logger.info(f"Updated score for evaluation {evaluation_run.evaluation_id}")

                return 1
        except Exception as e:
            logger.error(f"Error storing evaluation run {evaluation_run.run_id}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)

    def get_next_evaluation(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the next evaluation for a validator. Return None if not found.
        Excludes evaluations for banned miner hotkeys.
        """
        # For now, this is a manual process, but will be updated shortly to be automatic
        banned_hotkeys = ["5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9"]
        
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                SELECT e.evaluation_id, e.version_id, e.validator_hotkey, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
                FROM evaluations e
                JOIN agent_versions av ON e.version_id = av.version_id
                JOIN agents a ON av.agent_id = a.agent_id
                WHERE e.validator_hotkey = %s 
                AND e.status = 'waiting' 
                AND a.miner_hotkey != ALL(%s)
                ORDER BY e.created_at ASC 
                LIMIT 1;
            """, (validator_hotkey, banned_hotkeys))
                row = cursor.fetchone()
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
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_evaluation(self, evaluation_id: str) -> Optional[Evaluation]:
        """
        Get an evaluation from the database. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
                    FROM evaluations WHERE evaluation_id = %s
                """, (evaluation_id,))
                row = cursor.fetchone()
                if row:
                    logger.info(f"Evaluation {row[0]} retrieved from the database")
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
                logger.info(f"Evaluation {evaluation_id} not found in the database")
                return None
        finally:
            if conn:
                self.return_connection(conn)

    def get_evaluation_run(self, run_id: str) -> Optional[EvaluationRun]:
        """
        Get an evaluation run from the database. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT run_id, evaluation_id, swebench_instance_id, status, response, error, pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, fail_to_fail_success, solved, started_at, sandbox_created_at, patch_generated_at, eval_started_at, result_scored_at 
                    FROM evaluation_runs WHERE run_id = %s
                """, (run_id,))
                row = cursor.fetchone()
                if row:
                    return EvaluationRun(
                        run_id=row[0],
                        evaluation_id=row[1],
                        swebench_instance_id=row[2],
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
                    )
                logger.info(f"Evaluation run {run_id} not found in the database")
                return None
        finally:
            if conn:
                self.return_connection(conn)
    
    def delete_evaluation_runs(self, evaluation_id: str) -> int:
        """
        Delete all evaluation runs for a specific evaluation. Return the number of deleted runs.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM evaluation_runs WHERE evaluation_id = %s
                """, (evaluation_id,))
                deleted_count = cursor.rowcount
                logger.info(f"Deleted {deleted_count} evaluation runs for evaluation {evaluation_id}")
                return deleted_count
        except Exception as e:
            logger.error(f"Error deleting evaluation runs for evaluation {evaluation_id}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)
                
    def get_agent_by_hotkey(self, miner_hotkey: str) -> Agent:
        """
        Get an agent from the database. Return None if not found.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_agent_by_hotkey(miner_hotkey)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting agent by hotkey {miner_hotkey}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT agent_id, miner_hotkey, name, latest_version, created_at, last_updated 
                        FROM agents WHERE miner_hotkey = %s
                    """, (miner_hotkey,))
                    row = cursor.fetchone()
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
            finally:
                if conn:
                    self.return_connection(conn)
        
    def get_agent(self, agent_id: str) -> Agent:
        """
        Get an agent from the database. Return None if not found.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_agent(agent_id)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting agent {agent_id}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT agent_id, miner_hotkey, name, latest_version, created_at, last_updated 
                        FROM agents WHERE agent_id = %s
                    """, (agent_id,))
                    row = cursor.fetchone()
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
            finally:
                if conn:
                    self.return_connection(conn)
        
    def get_random_agent(self) -> Optional[AgentSummary]:
        """
        Get a random agent from the database. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT agent_id, miner_hotkey, name, latest_version
                    FROM agents
                    ORDER BY RANDOM()
                    LIMIT 1
                """)
                agent_row = cursor.fetchone()
                
                if not agent_row:
                    return None
                
                agent_id, miner_hotkey, name, latest_version_num = agent_row
                
                cursor.execute("""
                    SELECT version_id, agent_id, version_num, created_at, score
                    FROM agent_versions
                    WHERE agent_id = %s
                    ORDER BY version_num DESC
                    LIMIT 1
                """, (agent_id,))
                
                version_row = cursor.fetchone()
                if version_row:
                    agent_version = AgentVersion(
                        version_id=version_row[0],
                        agent_id=version_row[1],
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
        finally:
            if conn:
                self.return_connection(conn)
    
    def get_agent_by_version_id(self, version_id: str) -> Optional[Agent]:
        """
        Get an agent from the database by version_id. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT a.agent_id, a.miner_hotkey, a.name, a.latest_version, a.created_at, a.last_updated 
                    FROM agents a
                    JOIN agent_versions av ON a.agent_id = av.agent_id
                    WHERE av.version_id = %s
                """, (version_id,))
                row = cursor.fetchone()
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
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_agent_version(self, version_id: str) -> Optional[AgentVersion]:
        """
        Get an agent version from the database. Return None if not found.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_agent_version(version_id)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting agent version {version_id}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT version_id, agent_id, version_num, created_at, score 
                        FROM agent_versions WHERE version_id = %s
                    """, (version_id,))
                    row = cursor.fetchone()
                    if row:
                        return AgentVersion(
                            version_id=row[0],
                            agent_id=row[1],
                            version_num=row[2],
                            created_at=row[3],
                            score=row[4]
                        )
                    return None
            finally:
                if conn:
                    self.return_connection(conn)
        
    def get_evaluations_by_version_id(self, version_id: str) -> List[Evaluation]:
        """
        Get all evaluations for a version from the database. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
                    FROM evaluations WHERE version_id = %s
                """, (version_id,))
                rows = cursor.fetchall()
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
        finally:
            if conn:
                self.return_connection(conn)
        
    
    def get_latest_agent_version(self, agent_id: str) -> Optional[AgentVersion]:
        """
        Get the latest agent version from the database. Return None if not found.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_latest_agent_version(agent_id)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting latest agent version for {agent_id}, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT version_id, agent_id, version_num, created_at, score 
                        FROM agent_versions WHERE agent_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (agent_id,))
                    row = cursor.fetchone()
                    if row:
                        return AgentVersion(
                            version_id=row[0],
                            agent_id=row[1],
                            version_num=row[2],
                            created_at=row[3],
                            score=row[4]
                        )
                    return None
            finally:
                if conn:
                    self.return_connection(conn)
        
    def get_running_evaluation_by_validator_hotkey(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the running evaluation for a validator. Return None if not found.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
                    FROM evaluations WHERE validator_hotkey = %s AND status = 'running'
                """, (validator_hotkey,))
                row = cursor.fetchone()
                if row:
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
                return None
        finally:
            if conn:
                self.return_connection(conn)

    # def get_banned_hotkeys(self) -> List[str]:
    #     """
    #     Gets the list of miner hotkeys that are banned
    #     """
    #     conn = None
    #     try:
    #         conn = self.get_connection()
    #         with conn.cursor() as cursor:
    #             cursor.execute("""
    #                 SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
    #                 FROM evaluations WHERE validator_hotkey = %s AND status = 'running'
    #             """, (validator_hotkey,))
    #             row = cursor.fetchone()
    #             if row:
    #                 return Evaluation(
    #                 evaluation_id=row[0],
    #                 version_id=row[1],
    #                 validator_hotkey=row[2],
    #                 status=row[3],
    #                 terminated_reason=row[4],
    #                 created_at=row[5],
    #                 started_at=row[6],
    #                 finished_at=row[7],
    #                 score=row[8]
    #             )
    #             return None
    #     finally:
    #         if conn:
    #             self.return_connection(conn)
    
    def get_top_agents(self, num_agents: int) -> List[AgentSummary]:
        """
        Get the top agents from the database based on their latest scored version's score.
        Returns agents ordered by their latest version's score in descending order first,
        followed by all unscored agents.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
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
                    LIMIT %s
                """, (num_agents,))
                rows = cursor.fetchall()
                return [AgentSummary(
                    miner_hotkey=row[1],
                    name=row[2],
                    latest_version=AgentVersion(
                        version_id=row[6],
                        agent_id=row[0],
                        version_num=row[7],
                        created_at=row[8],
                        score=row[9]
                    ),
                    code=None
                ) for row in rows]
        finally:
            if conn:
                self.return_connection(conn)

    def get_top_agent(self) -> TopAgentHotkey:
        """
        Gets the top agents miner hotkey and version id from the database,
        where its been scored by at least 2 validators
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    WITH version_scores AS (                         -- 1.  score + validator count
                        SELECT
                            e.version_id,
                            AVG(e.score)                       AS avg_score,      -- use MAX() if preferred
                            COUNT(DISTINCT e.validator_hotkey) AS validator_cnt
                        FROM evaluations e
                        WHERE e.status = 'completed'
                        AND e.score  IS NOT NULL
                        GROUP BY e.version_id
                        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2
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
                    """
                )

                row = cursor.fetchone()

                return TopAgentHotkey(
                    miner_hotkey=row[0],
                    version_id=row[1],
                    avg_score=row[2]
                )
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_latest_agent(self, agent_id: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                if scored:
                    cursor.execute("""
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
                        WHERE a.agent_id = %s
                        AND latest_scored.score IS NOT NULL
                    """, (agent_id,))
                else:
                    cursor.execute("""
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
                        WHERE a.agent_id = %s
                    """, (agent_id,))
                
                row = cursor.fetchone()
                if row:
                    return AgentSummary(
                        miner_hotkey=row[1],
                        name=row[2],
                        latest_version=AgentVersion(
                            version_id=row[6],
                            agent_id=row[0],
                            version_num=row[7],
                            created_at=row[8],
                            score=row[9]
                        ),
                        code=None
                    )
                return None
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_latest_agent_by_miner_hotkey(self, miner_hotkey: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database by miner_hotkey. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                if scored:
                    cursor.execute("""
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
                        WHERE a.miner_hotkey = %s
                        AND latest_scored.score IS NOT NULL
                    """, (miner_hotkey,))
                else:
                    cursor.execute("""
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
                        WHERE a.miner_hotkey = %s
                    """, (miner_hotkey,))
                
                row = cursor.fetchone()
                if row:
                    return AgentSummary(
                        miner_hotkey=row[1],
                        name=row[2],
                        latest_version=AgentVersion(
                            version_id=row[6],
                            agent_id=row[0],
                            version_num=row[7],
                            created_at=row[8],
                            score=row[9]
                        ),
                        code=None
                    )
                return None
        finally:
            if conn:
                self.return_connection(conn)

    def get_recent_executions(self, num_executions: int) -> List[Execution]:
        """
        Gets the X most recently created evaluations, and returns a list of objects with the AgentVersion, Agent, Evaluation, and Runs
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
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
                    LIMIT %s
                """, (num_executions,))
                
                rows = cursor.fetchall()
                executions = []
                
                for row in rows:
                    evaluation_id = row[0]
                    
                    # Get evaluation runs for this evaluation
                    cursor.execute("""
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
                        WHERE evaluation_id = %s
                    """, (evaluation_id,))
                    
                    run_rows = cursor.fetchall()
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
                    executions.append(execution)
                
                return executions
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_num_agents(self) -> int:
        """
        Get the number of agents in the database.
        """
        try:
            return self.sqlalchemy_manager.get_num_agents()
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting agent count, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT COUNT(*) FROM agents
                    """)
                    return cursor.fetchone()[0]
            finally:
                if conn:
                    self.return_connection(conn)

    def get_num_agent_versions(self) -> int:
        """
        Get the number of agent versions in the database.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM agent_versions")
                return cursor.fetchone()[0]
        finally:
            if conn:
                self.return_connection(conn)

    def get_num_evaluations(self) -> int:
        """
        Get the number of evaluations in the database.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM evaluations")
                return cursor.fetchone()[0]
        finally:
            if conn:
                self.return_connection(conn)

    def get_num_evaluation_runs(self) -> int:
        """
        Get the number of evaluation runs in the database.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM evaluation_runs")
                return cursor.fetchone()[0]
        finally:
            if conn:
                self.return_connection(conn)

    def get_evaluation_status_counts(self) -> dict:
        """
        Get counts of evaluations grouped by status.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT status, COUNT(*) 
                    FROM evaluations 
                    GROUP BY status
                """)
                return dict(cursor.fetchall())
        finally:
            if conn:
                self.return_connection(conn)

    def get_evaluation_run_status_counts(self) -> dict:
        """
        Get counts of evaluation runs grouped by status.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT status, COUNT(*) 
                    FROM evaluation_runs 
                    GROUP BY status
                """)
                return dict(cursor.fetchall())
        finally:
            if conn:
                self.return_connection(conn)

    def get_database_summary(self) -> dict:
        """
        Get a summary of row counts for all tables.
        """
        return {
            'agents': self.get_num_agents(),
            'agent_versions': self.get_num_agent_versions(),
            'evaluations': self.get_num_evaluations(),
            'evaluation_runs': self.get_num_evaluation_runs(),
            'banned_hotkeys': self._count_table_rows('banned_hotkeys'),
            'weights_history': self._count_table_rows('weights_history')
        }

    def _count_table_rows(self, table_name: str) -> int:
        """
        Helper method to count rows in any table.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                return cursor.fetchone()[0]
        finally:
            if conn:
                self.return_connection(conn)

    def get_latest_execution_by_agent(self, agent_id: str) -> Optional[Execution]:
        """
        Get the current execution for an agent with priority:
        1. Most recent running evaluation
        2. Most recent scored evaluation  
        3. None if neither exists
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                # First, try to get the most recent running evaluation
                cursor.execute("""
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
                    WHERE a.agent_id = %s AND e.status = 'running'
                    ORDER BY e.created_at DESC 
                    LIMIT 1
                """, (agent_id,))
                
                row = cursor.fetchone()
                
                # If no running evaluation, get the most recent scored evaluation
                if not row:
                    cursor.execute("""
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
                        WHERE a.agent_id = %s AND av.score IS NOT NULL
                        ORDER BY e.created_at DESC 
                        LIMIT 1
                    """, (agent_id,))
                    
                    row = cursor.fetchone()
                
                # If still no row found, return None
                if not row:
                    return None
                
                evaluation_id = row[0]
                
                # Get evaluation runs for this evaluation
                cursor.execute("""
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
                    WHERE evaluation_id = %s
                """, (evaluation_id,))
                
                run_rows = cursor.fetchall()
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
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_agent_summary(self, agent_id: str = None, miner_hotkey: str = None) -> AgentSummaryResponse:
        """
        Get a summary of an agent including its details, latest version, and all versions.
        Returns AgentSummaryResponse with agent_details, latest_version, and all_versions.
        Uses optimized single-query approach with JSON aggregation.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                # Use agent_id if provided, otherwise use miner_hotkey
                if agent_id:
                    search_param = agent_id
                    search_type = "ID"
                    where_clause = "agent_id = %s"
                else:
                    search_param = miner_hotkey
                    search_type = "miner hotkey"
                    where_clause = "miner_hotkey = %s"
                
                # Optimized single round-trip query with JSON aggregation
                cursor.execute(f"""
                    WITH a AS (
                        SELECT agent_id, miner_hotkey, name, created_at
                        FROM   agents
                        WHERE  {where_clause}
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
                """, (search_param,))
                
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Agent with {search_type} {search_param} not found")
                
                # Parse the result
                agent_id, miner_hotkey, name, created_at, latest_version_json, all_versions_json = row
                
                # Create agent details
                agent_details = AgentDetailsNew(
                    agent_id=agent_id,
                    miner_hotkey=miner_hotkey,
                    name=name,
                    created_at=created_at
                )
                
                # Parse latest version from JSON
                latest_version = None
                if latest_version_json:
                    latest_version = AgentVersionNew(
                        version_id=latest_version_json['version_id'],
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
                            version_id=version_data['version_id'],
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
            logger.error(f"Error getting agent summary for {search_param}: {str(e)}")
            return None
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_agent_version_new(self, version_id: str) -> AgentVersionDetails:
        """
        Get detailed information about an agent version including its execution data.
        Returns AgentVersionDetails with agent_version and execution.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT version_id, version_num, created_at, score
                    FROM agent_versions 
                    WHERE version_id = %s
                """, (version_id,))
                
                version_row = cursor.fetchone()
                if not version_row:
                    raise ValueError(f"Agent version with ID {version_id} not found")
                
                agent_version = AgentVersionNew(
                    version_id=version_row[0],
                    version_num=version_row[1],
                    created_at=version_row[2],
                    score=version_row[3],
                    code=None
                )
                
                cursor.execute("""
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
                    WHERE version_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (version_id,))
                
                execution_row = cursor.fetchone()
                if not execution_row:
                    raise ValueError(f"No execution found for version {version_id}")
                
                evaluation_id = execution_row[0]
                
                cursor.execute("""
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
                    WHERE evaluation_id = %s
                    ORDER BY started_at
                """, (evaluation_id,))
                
                run_rows = cursor.fetchall()
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
                
                execution = ExecutionNew(
                    evaluation_id=execution_row[0],
                    agent_version_id=execution_row[1],
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
            logger.error(f"Error getting agent version details for {version_id}: {str(e)}")
            return None
        finally:
            if conn:
                self.return_connection(conn)

    def get_evaluations(self, version_id: str) -> List[ExecutionNew]:
        """
        Get all evaluations for a specific agent version, including their evaluation runs.
        Returns a list of ExecutionNew objects.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:

                cursor.execute("""
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
                    WHERE version_id = %s
                    ORDER BY created_at DESC
                """, (version_id,))
                
                evaluation_rows = cursor.fetchall()
                executions = []
                
                for eval_row in evaluation_rows:
                    evaluation_id = eval_row[0]
                    
                    cursor.execute("""
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
                        WHERE evaluation_id = %s
                        ORDER BY started_at
                    """, (evaluation_id,))
                    
                    run_rows = cursor.fetchall()
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
                    
                    # Create ExecutionNew object
                    execution = ExecutionNew(
                        evaluation_id=eval_row[0],
                        agent_version_id=eval_row[1],
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
            logger.error(f"Error getting evaluations for version {version_id}: {str(e)}")
            return []
        finally:
            if conn:
                self.return_connection(conn)

    def store_weights(self, miner_weights: dict, time_since_last_update=None) -> int:
        """
        Store miner weights in the weights_history table. Return 1 if successful, 0 if not.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.store_weights(miner_weights, time_since_last_update)
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for storing weights, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                conn.autocommit = True
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO weights_history (timestamp, time_since_last_update, miner_weights)
                        VALUES (%s, %s, %s)
                    """, (datetime.now(), time_since_last_update, json.dumps(miner_weights)))
                    logger.info(f"Weights stored successfully with {len(miner_weights)} miners via fallback")
                    return 1
            except Exception as fallback_error:
                logger.error(f"Error storing weights: {str(fallback_error)}")
                return 0
            finally:
                if conn:
                    self.return_connection(conn)

    def get_latest_weights(self) -> Optional[dict]:
        """
        Get the most recent weights from the weights_history table. Return None if not found.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_latest_weights()
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting latest weights, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT miner_weights, timestamp, time_since_last_update
                        FROM weights_history 
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    """)
                    row = cursor.fetchone()
                    
                    if row:
                        return {
                            'weights': row[0],  # JSONB is already a dict, no need to parse
                            'timestamp': row[1],
                            'time_since_last_update': row[2]
                        }
                    return None
            except Exception as fallback_error:
                logger.error(f"Error getting latest weights: {str(fallback_error)}")
                return None
            finally:
                if conn:
                    self.return_connection(conn)

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

    def get_top_miner_fraction_last_24h(self, miner_hotkey: str) -> float:
        """
        Calculate the fraction of the last 24 hours that a miner held the top weight position.
        Returns a value between 0.0 and 1.0 representing the fraction of time they were the top miner.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
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
                        WHERE top_miner_hotkey = %s
                    )
                    SELECT COALESCE(total_top_time, 0.0) as fraction
                    FROM miner_top_time
                """, (miner_hotkey,))
                
                result = cursor.fetchone()
                if result:
                    fraction = float(result[0])
                    logger.info(f"Miner {miner_hotkey} was top miner for {fraction:.4f} fraction of the last 24 hours")
                    return fraction
                else:
                    logger.warning(f"No weight history found for miner {miner_hotkey} in the last 24 hours")
                    return 0.0
                    
        except Exception as e:
            logger.error(f"Error calculating top miner fraction for {miner_hotkey}: {str(e)}")
            return 0.0
        finally:
            if conn:
                self.return_connection(conn)

    def get_current_top_miner(self) -> Optional[str]:
        """
        Get the miner hotkey with the highest weight from the most recent weights snapshot.
        Returns the miner hotkey if found, None if no weights are available.
        Uses SQLAlchemy for better maintainability.
        """
        try:
            return self.sqlalchemy_manager.get_current_top_miner()
        except Exception as e:
            logger.error(f"SQLAlchemy method failed for getting current top miner, falling back to raw SQL: {str(e)}")
            # Fallback to original method
            conn = None
            try:
                conn = self.get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT miner_weights
                        FROM weights_history 
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    """)
                    row = cursor.fetchone()
                    
                    if not row or not row[0]:
                        logger.warning("No weights found in database")
                        return None
                    
                    # miner_weights is already a dict, find the miner with highest weight
                    miner_weights = row[0]
                    if not miner_weights:
                        logger.warning("Empty weights found in the latest snapshot")
                        return None
                    
                    # Find the miner with the highest weight
                    top_miner_hotkey = max(miner_weights.items(), key=lambda x: float(x[1]))[0]
                    top_weight = miner_weights[top_miner_hotkey]
                    
                    logger.info(f"Current top miner: {top_miner_hotkey} with weight {top_weight}")
                    return top_miner_hotkey
                        
            except Exception as fallback_error:
                logger.error(f"Error getting current top miner: {str(fallback_error)}")
                return None
            finally:
                if conn:
                    self.return_connection(conn)

    def get_weights_history_last_24h_with_prior(self) -> List[WeightsData]:
        """
        Returns all rows from weights_history with timestamp >= NOW() - INTERVAL '24 hours',
        plus one row immediately before that window (if it exists), ordered by timestamp ascending.
        Returns a list of WeightsData models.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
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
                """)
                rows = cursor.fetchall()
                return [WeightsData(
                    id=row[0],
                    timestamp=row[1],
                    time_since_last_update=row[2],
                    miner_weights=row[3]
                ) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching weights_history for last 24h with prior: {str(e)}")
            return []
        finally:
            if conn:
                self.return_connection(conn)
        
    def get_queue_info(self, version_id: str) -> List[QueueInfo]:
        """
        For a given version_id, for each evaluation:
        - If running, place_in_queue=0
        - If waiting, place_in_queue=(number of waiting for that validator with earlier created_at)+1
        - If completed, place_in_queue=-1
        - If replaced, place_in_queue=None
        Returns a list of QueueInfo(validator_hotkey, place_in_queue) for each evaluation for the version.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT validator_hotkey, created_at, status
                    FROM evaluations
                    WHERE version_id = %s
                    ORDER BY created_at ASC
                """, (version_id,))
                evals = cursor.fetchall()
                if not evals:
                    return []

                queue_info_list = []
                for validator_hotkey, created_at, status in evals:
                    if status == "running":
                        place_in_queue = 0
                    elif status == "waiting":
                        cursor.execute("""
                            SELECT COUNT(*)
                            FROM evaluations
                            WHERE status = 'waiting' AND validator_hotkey = %s AND created_at < %s
                        """, (validator_hotkey, created_at))
                        place_in_queue = cursor.fetchone()[0] + 1
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
            logger.error(f"Error getting queue info for version {version_id}: {str(e)}")
            return []
        finally:
            if conn:
                self.return_connection(conn)

    def create_evaluations_for_validator(self, validator_hotkey: str) -> int:
        """
        Create evaluations for a validator for all agent versions created in the last 24 hours
        that are the latest version for their agent and don't already have an evaluation
        for this validator. Returns the number of evaluations created.
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("""
                    WITH latest_versions_last_24h AS (
                        SELECT av.version_id, av.agent_id, av.version_num, av.created_at
                        FROM agent_versions av
                        INNER JOIN agents a ON av.agent_id = a.agent_id
                        WHERE av.created_at >= NOW() - INTERVAL '24 hours'
                        AND av.version_num = a.latest_version
                    ),
                    missing_evaluations AS (
                        SELECT lv.version_id
                        FROM latest_versions_last_24h lv
                        WHERE NOT EXISTS (
                            SELECT 1 
                            FROM evaluations e 
                            WHERE e.version_id = lv.version_id 
                            AND e.validator_hotkey = %s
                        )
                    )
                    SELECT version_id FROM missing_evaluations
                """, (validator_hotkey,))
                
                missing_version_ids = [row[0] for row in cursor.fetchall()]
                
                evaluations_created = 0
                for version_id in missing_version_ids:
                    evaluation_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at, started_at, finished_at, terminated_reason, score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (evaluation_id, version_id, validator_hotkey, 'waiting', datetime.now(), None, None, None, None))
                    evaluations_created += cursor.rowcount
                
                logger.info(f"Created {evaluations_created} evaluations for validator {validator_hotkey}")
                return evaluations_created
                
        except Exception as e:
            logger.error(f"Error creating evaluations for validator {validator_hotkey}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)

    def ban_agent(self, agent_id: str) -> int:
        """
        Ban an agent by deleting it and all related data from the database.
        Deletes in order: evaluation_runs -> evaluations -> agent_versions -> agents
        to respect foreign key constraints.
        Returns the number of rows deleted (1 if successful, 0 if agent not found).
        """
        conn = None
        try:
            conn = self.get_connection()
            conn.autocommit = True
            with conn.cursor() as cursor:
                # First, check if the agent exists
                cursor.execute("SELECT agent_id FROM agents WHERE agent_id = %s", (agent_id,))
                if not cursor.fetchone():
                    logger.warning(f"Agent {agent_id} not found for deletion")
                    return 0
                
                # Delete in order to respect foreign key constraints:
                # 1. Delete evaluation_runs for evaluations of this agent's versions
                cursor.execute("""
                    DELETE FROM evaluation_runs 
                    WHERE evaluation_id IN (
                        SELECT e.evaluation_id 
                        FROM evaluations e
                        JOIN agent_versions av ON e.version_id = av.version_id
                        WHERE av.agent_id = %s
                    )
                """, (agent_id,))
                runs_deleted = cursor.rowcount
                
                # 2. Delete evaluations for this agent's versions
                cursor.execute("""
                    DELETE FROM evaluations 
                    WHERE version_id IN (
                        SELECT version_id 
                        FROM agent_versions 
                        WHERE agent_id = %s
                    )
                """, (agent_id,))
                evaluations_deleted = cursor.rowcount
                
                # 3. Delete agent_versions for this agent
                cursor.execute("DELETE FROM agent_versions WHERE agent_id = %s", (agent_id,))
                versions_deleted = cursor.rowcount
                
                # 4. Finally delete the agent
                cursor.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))
                agent_deleted = cursor.rowcount
                
                logger.info(f"Banned agent {agent_id}: deleted {runs_deleted} evaluation runs, {evaluations_deleted} evaluations, {versions_deleted} versions, {agent_deleted} agent")
                return agent_deleted
                
        except Exception as e:
            logger.error(f"Error banning agent {agent_id}: {str(e)}")
            return 0
        finally:
            if conn:
                self.return_connection(conn)
    