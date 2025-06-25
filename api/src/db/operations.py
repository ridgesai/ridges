import os
from typing import Optional, List
import psycopg2
from dotenv import load_dotenv
from api.src.utils.models import Agent, AgentVersion, EvaluationRun, Evaluation, AgentSummary, Execution
from api.src.utils.logging_utils import get_logger

load_dotenv()

logger = get_logger(__name__)

class DatabaseManager:
    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv('AWS_RDS_PLATFORM_ENDPOINT'),
            user=os.getenv('AWS_MASTER_USERNAME'),
            password=os.getenv('AWS_MASTER_PASSWORD'),
            database=os.getenv('AWS_RDS_PLATFORM_DB_NAME'),
            sslmode='require',
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
            # Keepalive stuff is for a bug fix, look into it later
        )
        self.conn.autocommit = True
        self.init_tables()

    def init_tables(self):
        """
        Check if required tables exist and create them if they don't.
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('agents', 'agent_versions', 'evaluations', 'evaluation_runs', 'weights_history')
                """)
                existing_tables = [row[0] for row in cursor.fetchall()]
            
            logger.info(f"Existing database tables: {existing_tables}")

            if len(existing_tables) == 5:
                logger.info("All required tables already exist")
                return
            
            logger.info("Not all tables exist, initializing them")

            schema_path = os.path.join(os.path.dirname(__file__), 'postgres_schema.sql')
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            
            with self.conn.cursor() as cursor:
                cursor.execute(schema_sql)
            
            logger.info("Database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {str(e)}")
            raise

    def close(self):
        """
        Close the database connection.
        """
        if self.conn:
            self.conn.close()
        
    def store_agent(self, agent: Agent) -> int:
        """
        Store an agent in the database. If the agent already exists, update latest_version and last_updated. Return 1 if successful, 0 if not.
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agents (agent_id, miner_hotkey, name, latest_version, created_at, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (agent_id) DO UPDATE SET
                        latest_version = EXCLUDED.latest_version,
                        last_updated = EXCLUDED.last_updated
                """, (agent.agent_id, agent.miner_hotkey, agent.name, agent.latest_version, agent.created_at, agent.last_updated))
                logger.info(f"Agent {agent.agent_id} stored successfully")
                return 1
        except Exception as e:
            logger.error(f"Error storing agent {agent.agent_id}: {str(e)}")
            return 0
        
    def store_agent_version(self, agent_version: AgentVersion) -> int:
        """
        Store an agent version in the database. Return 1 if successful, 0 if not. If the agent version already exists, update the score.
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agent_versions (version_id, agent_id, version_num, created_at, score)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (version_id) DO UPDATE SET
                        score = EXCLUDED.score
                    """, (agent_version.version_id, agent_version.agent_id, agent_version.version_num, agent_version.created_at, agent_version.score))
                logger.info(f"Agent version {agent_version.version_id} stored successfully")
                return 1
        except Exception as e:
            logger.error(f"Error storing agent version {agent_version.version_id}: {str(e)}")
            return 0
    
    def store_evaluation(self, evaluation: Evaluation, score: bool = False) -> int:
        """
        Store an evaluation in the database. Return 1 if successful, 0 if not. If the evaluation already exists, update the status, started_at, and finished_at.
        If score=True, dynamically calculate the score from evaluation runs.
        """
        try:
            if score:
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT COUNT(*) as total_runs, 
                               COUNT(CASE WHEN solved = true THEN 1 END) as solved_runs
                        FROM evaluation_runs 
                        WHERE evaluation_id = %s
                    """, (evaluation.evaluation_id,))
                    result = cursor.fetchone()
                    total_runs = result[0]
                    solved_runs = result[1]
                    
                    if total_runs > 0:
                        evaluation.score = solved_runs / total_runs
                    else:
                        evaluation.score = 0.0
                    
                    logger.info(f"Calculated score for evaluation {evaluation.evaluation_id}: {evaluation.score} ({solved_runs}/{total_runs})")
            
            with self.conn.cursor() as cursor:
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
        
    def update_agent_version_score(self, version_id: str) -> int:
        """
        Update the score for an agent version. Return 1 if successful, 0 if not.
        """
        try:
            with self.conn.cursor() as cursor:

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

    def store_evaluation_run(self, evaluation_run: EvaluationRun) -> int:
        """
        Store an evaluation run in the database. Return 1 if successful, 0 if not.
        """
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO evaluation_runs (run_id, evaluation_id, swebench_instance_id, response, error, pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, fail_to_fail_success, solved, started_at, finished_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        response = EXCLUDED.response,
                        error = EXCLUDED.error,
                        pass_to_fail_success = EXCLUDED.pass_to_fail_success,
                        fail_to_pass_success = EXCLUDED.fail_to_pass_success,
                        pass_to_pass_success = EXCLUDED.pass_to_pass_success,
                        fail_to_fail_success = EXCLUDED.fail_to_fail_success,
                        solved = EXCLUDED.solved,
                        finished_at = EXCLUDED.finished_at
                """, (evaluation_run.run_id, evaluation_run.evaluation_id, evaluation_run.swebench_instance_id, evaluation_run.response, evaluation_run.error, evaluation_run.pass_to_fail_success, evaluation_run.fail_to_pass_success, evaluation_run.pass_to_pass_success, evaluation_run.fail_to_fail_success, evaluation_run.solved, evaluation_run.started_at, evaluation_run.finished_at))
                logger.info(f"Evaluation run {evaluation_run.run_id} stored successfully")
                return 1
        except Exception as e:
            logger.error(f"Error storing evaluation run {evaluation_run.run_id}: {str(e)}")
            return 0

    def get_next_evaluation(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the next evaluation for a validator. Return None if not found.
        """
        with self.conn.cursor() as cursor:
            cursor.execute("""
                SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
                FROM evaluations WHERE validator_hotkey = %s AND status = 'waiting' ORDER BY created_at ASC LIMIT 1;
            """, (validator_hotkey,))
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
            logger.info(f"No pending evaluations found for validator with hotkey {validator_hotkey}")
            return None
        
    def get_evaluation(self, evaluation_id: str) -> Optional[Evaluation]:
        """
        Get an evaluation from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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

    def get_evaluation_run(self, run_id: str) -> Optional[EvaluationRun]:
        """
        Get an evaluation run from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
            cursor.execute("""
                SELECT run_id, evaluation_id, swebench_instance_id, response, error, pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, fail_to_fail_success, solved, started_at, finished_at 
                FROM evaluation_runs WHERE run_id = %s
            """, (run_id,))
            row = cursor.fetchone()
            if row:
                return EvaluationRun(
                    run_id=row[0],
                    evaluation_id=row[1],
                    swebench_instance_id=row[2],
                    response=row[3],
                    error=row[4],
                    pass_to_fail_success=row[5],
                    fail_to_pass_success=row[6],
                    pass_to_pass_success=row[7],
                    fail_to_fail_success=row[8],
                    solved=row[9],
                    started_at=row[10],
                    finished_at=row[11]
                )
            logger.info(f"Evaluation run {run_id} not found in the database")
            return None
                
    def get_agent_by_hotkey(self, miner_hotkey: str) -> Agent:
        """
        Get an agent from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
        
    def get_agent(self, agent_id: str) -> Agent:
        """
        Get an agent from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
    
    def get_agent_by_version_id(self, version_id: str) -> Optional[Agent]:
        """
        Get an agent from the database by version_id. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
        
    def get_agent_version(self, version_id: str) -> Optional[AgentVersion]:
        """
        Get an agent version from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
        
    def get_evaluations_by_version_id(self, version_id: str) -> List[Evaluation]:
        """
        Get all evaluations for a version from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
        
    
    def get_latest_agent_version(self, agent_id: str) -> Optional[AgentVersion]:
        """
        Get the latest agent version from the database. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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
        
    def get_running_evaluation_by_validator_hotkey(self, validator_hotkey: str) -> Optional[Evaluation]:
        """
        Get the running evaluation for a validator. Return None if not found.
        """
        with self.conn.cursor() as cursor:
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

    def get_top_agents(self, num_agents: int) -> List[AgentSummary]:
        """
        Get the top agents from the database based on their latest scored version's score.
        Returns agents ordered by their latest version's score in descending order.
        Returns None if no scored agents are found.
        """
        with self.conn.cursor() as cursor:
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
                WHERE latest_scored.score IS NOT NULL
                ORDER BY latest_scored.score DESC 
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
        
    def get_latest_agent(self, agent_id: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        with self.conn.cursor() as cursor:
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
        
    def get_latest_agent_by_miner_hotkey(self, miner_hotkey: str, scored: bool) -> Optional[AgentSummary]:
        """
        Get the latest agent from the database by miner_hotkey. Return None if not found.
        If scored=True, only returns agents that have a scored version.
        """
        with self.conn.cursor() as cursor:
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

    def get_recent_executions(self, num_executions: int) -> List[Execution]:
        """
        Gets the X most recently created evaluations, and returns a list of objects with the AgentVersion, Agent, Evaluation, and Runs
        """
        with self.conn.cursor() as cursor:
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
                        response,
                        error,
                        pass_to_fail_success,
                        fail_to_pass_success,
                        pass_to_pass_success,
                        fail_to_fail_success,
                        solved,
                        started_at,
                        finished_at
                    FROM evaluation_runs 
                    WHERE evaluation_id = %s
                """, (evaluation_id,))
                
                run_rows = cursor.fetchall()
                evaluation_runs = [
                    EvaluationRun(
                        run_id=run_row[0],
                        evaluation_id=run_row[1],
                        swebench_instance_id=run_row[2],
                        response=run_row[3],
                        error=run_row[4],
                        pass_to_fail_success=run_row[5],
                        fail_to_pass_success=run_row[6],
                        pass_to_pass_success=run_row[7],
                        fail_to_fail_success=run_row[8],
                        solved=run_row[9],
                        started_at=run_row[10],
                        finished_at=run_row[11]
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
        
    def get_num_agents(self) -> int:
        """
        Get the number of agents in the database.
        """
        with self.conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM agents
            """)
            return cursor.fetchone()[0]

    def get_latest_execution_by_agent(self, agent_id: str) -> Optional[Execution]:
        """
        Get the current execution for an agent with priority:
        1. Most recent running evaluation
        2. Most recent scored evaluation  
        3. None if neither exists
        """
        with self.conn.cursor() as cursor:
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
                    response,
                    error,
                    pass_to_fail_success,
                    fail_to_pass_success,
                    pass_to_pass_success,
                    fail_to_fail_success,
                    solved,
                    started_at,
                    finished_at
                FROM evaluation_runs 
                WHERE evaluation_id = %s
            """, (evaluation_id,))
            
            run_rows = cursor.fetchall()
            evaluation_runs = [
                EvaluationRun(
                    run_id=run_row[0],
                    evaluation_id=run_row[1],
                    swebench_instance_id=run_row[2],
                    response=run_row[3],
                    error=run_row[4],
                    pass_to_fail_success=run_row[5],
                    fail_to_pass_success=run_row[6],
                    pass_to_pass_success=run_row[7],
                    fail_to_fail_success=run_row[8],
                    solved=run_row[9],
                    started_at=run_row[10],
                    finished_at=run_row[11]
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

    def store_weights(self, miner_weights: dict, time_since_last_update=None) -> int:
        """
        Store miner weights in the weights_history table. Return 1 if successful, 0 if not.
        """
        try:
            import json
            from datetime import datetime
            
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO weights_history (timestamp, time_since_last_update, miner_weights)
                    VALUES (%s, %s, %s)
                """, (datetime.now(), time_since_last_update, json.dumps(miner_weights)))
                logger.info(f"Weights stored successfully with {len(miner_weights)} miners")
                return 1
        except Exception as e:
            logger.error(f"Error storing weights: {str(e)}")
            return 0

    def get_latest_weights(self) -> Optional[dict]:
        """
        Get the most recent weights from the weights_history table. Return None if not found.
        """
        try:
            with self.conn.cursor() as cursor:
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
