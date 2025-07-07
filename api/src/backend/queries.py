from typing import Optional, List
import asyncpg
from api.src.utils.models import AgentVersionDetails
from pydantic import BaseModel, Field
import logging
from functools import wraps

# Agent related functions 
'''
- store agent
- store agent version
- store evaluation - when a agent is submitted we do this for connected validators
- get next evaluation for validator
- get a specific evaluation or evaluation run
- delete_evaluation runs - for when a disconnection happens
- get agent by hotkey or agent id
- get agent by version id 
- get evals by version id 
- get agent version
- get latest agent version
- get current evaluations (for dashboard)
- get running evals by validator hotkey
- get top agents 
- get top agent
- get latest agent (AgentSummary)
- get latest agent by miner hotkey

'''

from datetime import datetime

logger = logging.getLogger(__name__)

def db_operation(func):
    """Decorator to handle database operations with logging and transaction rollback"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        conn = args[0]  # First argument should be the connection
        async with conn.transaction():
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Database operation failed in {func.__name__}: {e}")
                # Context manager will roll back transaction, reversing any failed commits
                raise

    return wrapper

class Agent(BaseModel):
    miner_hotkey: str
    name: str
    latest_version: int
    created_at: datetime
    last_updated: datetime

class AgentVersion(BaseModel): 
    version_id: str
    miner_hotkey: str
    version_num: int
    created_at: datetime
    score: float

from enum import Enum

class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running" 
    completed = "completed"
    replaced = "replaced"

class Evaluation(BaseModel):
    evaluation_id: str
    version_id: str
    validator_hotkey: str
    status: EvaluationStatus
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    score: Optional[float]

class AgentWithHydratedLatestVersion(Agent):
    latest_version: AgentVersion

'''
Agent upload related functions
'''
@db_operation
async def create_agent(conn: asyncpg.Connection, agent: Agent):
    await conn.execute("""
        INSERT INTO agents (miner_hotkey, name, latest_version, created_at, last_updated)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (miner_hotkey) DO UPDATE 
        SET name = EXCLUDED.name,
            latest_version = EXCLUDED.latest_version,
            last_updated = EXCLUDED.last_updated
    """, agent.miner_hotkey, agent.name, agent.latest_version, agent.created_at, agent.last_updated)

@db_operation
async def store_agent_version(conn: asyncpg.Connection, agent_version: AgentVersion):
    await conn.execute("""
        INSERT INTO agent_versions (version_id, miner_hotkey, version_num, created_at, score)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (version_id) DO UPDATE 
        SET score = EXCLUDED.score
    """, agent_version.version_id, agent_version.miner_hotkey, agent_version.version_num, agent_version.created_at, agent_version.score)

# TODO: combine first query with second. this should be a batch call.
@db_operation
async def create_evaluations_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> int:
    agent_versions = await conn.fetch(
        "SELECT av.version_id "
        "FROM agent_versions av "
        "JOIN agents a ON av.miner_hotkey = a.miner_hotkey "
        "WHERE av.created_at >= NOW() - INTERVAL '24 hours' "
        "AND av.version_num = a.latest_version "
        "ORDER BY av.created_at DESC"
    )

    if not agent_versions:
        raise Exception("No recent agent versions found")
    
    evaluations_created = 0 
    import uuid

    for version_row in agent_versions:
        version_id = version_row[0]
        
        # Check if evaluation already exists
        existing_evaluation = await conn.fetchrow(
            "SELECT evaluation_id FROM evaluations "
            "WHERE version_id = $1 AND validator_hotkey = $2",
            version_id, validator_hotkey
        )
        
        if existing_evaluation:
            logger.debug(f"Evaluation already exists for version {version_id} and validator {validator_hotkey}")
            continue
        
        # Create new evaluation
        evaluation_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
            VALUES ($1, $2, $3, $4, $5)
        """, evaluation_id, version_id, validator_hotkey, 'waiting', datetime.now())
        
        evaluations_created += 1
    
    return evaluations_created

@db_operation
async def get_agent_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Agent]:
    """Get agent by hotkey. Auto-create evaluations for connected validators"""
    result = await conn.fetch(
        "SELECT miner_hotkey, name, latest_version, created_at, last_updated "
        "FROM agents WHERE miner_hotkey = $1",
        miner_hotkey
    )

    if not result:
        return None
    
    return Agent(**dict(result[0]))

'''
Websocket connection related endpoints
'''
async def get_next_evaluation():
    pass 
    # - Get pending evaluation for validator

async def get_agent_version_for_validator():
    pass 
    # - Get agent details for evaluation

async def start_evaluation():
    pass 
    # - Mark evaluation as started

async def get_running_evaluation_by_validator_hotkey():
    pass 
    # - Check for existing running eval

async def reset_running_evaluations():
    pass 
    # - Reset stuck evaluations on disconnect


'''
Validator data pulling
'''
async def get_evaluation():
    pass 
    # - Get specific evaluation details

async def get_evaluations_by_version_id():
    pass 
    # - Get all evaluations for a version

async def get_queue_info():
    pass 
    # - Get queue position per validator

async def get_current_evaluations():
    pass 
    # - Get all running evaluations

async def get_latest_weights():
    pass 
    # - Get current network weights

'''
Evaluation Creation/Upserts
'''
async def create_evaluation():
    pass 
    # - Create new evaluation

async def store_evaluation():
    pass 
    # - Store/update evaluation

async def upsert_evaluation_run():
    pass 
    # - Store individual test run results

async def finish_evaluation():
    pass 
    # - Mark evaluation as completed

async def delete_evaluation_runs():
    pass 
    # - Clean up runs on disconnect


'''
Cleanup and maintainance
'''

async def clean_hanging_evaluations():
    pass 
    # - Reset stuck evaluations

async def clean_timed_out_evaluations():
    pass 
    # - Reset timed-out evaluations (>150min)

async def get_pool_status():
    pass 
    # - Monitor DB connection health
