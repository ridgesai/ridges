## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime, timezone
from uuid import UUID

from fastapi import WebSocket
from pydantic import BaseModel, Field
from typing import Literal, Optional, TYPE_CHECKING
from enum import Enum



class MinerAgent(BaseModel): 
    """Maps to the agent_versions table"""
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str
    agent_summary: Optional[str] = None

class AgentWithHydratedCode(MinerAgent):
    code: str

class MinerAgentWithScores(MinerAgent):
    """MinerAgent with computed scores by set_id"""
    score: Optional[float]
    set_id: Optional[int]
    approved: Optional[bool] = None

class MinerAgentScored(BaseModel):
    """Maps to the agent_scores materialized view with precomputed scoring logic"""
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str
    agent_summary: Optional[str] = None
    set_id: int
    approved: bool
    validator_count: int
    final_score: float
    
    @staticmethod
    async def check_for_new_high_score(conn, version_id: UUID) -> dict:
        """
        Check if version_id scored higher than all approved agents within the same set_id.
        Uses the agent_scores materialized view for performance.
        
        Returns dict with:
        - high_score_detected: bool
        - agent details if high score detected
        - reason if no high score detected
        """
        # Get the current agent's score from materialized view
        agent_score_result = await conn.fetchrow("""
            SELECT 
                version_id, miner_hotkey, agent_name, version_num,
                created_at, status, agent_summary, set_id, 
                approved, validator_count, final_score
            FROM agent_scores
            WHERE version_id = $1
        """, version_id)
        
        if not agent_score_result:
            return {
                "high_score_detected": False, 
                "reason": "Agent not found or no valid score available (need 2+ validators)"
            }
        
        current_score = agent_score_result['final_score']
        current_set_id = agent_score_result['set_id']
        
        # Get the highest score among ALL approved agents within the same set_id
        max_approved_result = await conn.fetchrow("""
            SELECT MAX(ass.final_score) as max_score
            FROM agent_scores ass
            WHERE ass.approved = true AND ass.set_id = $1
        """, current_set_id)
        
        max_approved_score = max_approved_result['max_score'] if max_approved_result else None
        
        # Check if this beats all approved agents
        if max_approved_score is None or current_score > max_approved_score:
            return {
                "high_score_detected": True,
                "agent_name": agent_score_result['agent_name'],
                "miner_hotkey": agent_score_result['miner_hotkey'], 
                "version_id": str(version_id),
                "version_num": agent_score_result['version_num'],
                "new_score": float(current_score),
                "previous_max_score": float(max_approved_score) if max_approved_score else 0.0,
                "set_id": current_set_id
            }

        return {
            "high_score_detected": False,
            "reason": f"Score {current_score:.4f} does not beat max approved score {max_approved_score:.4f} on set_id {current_set_id}"
        }
    
    @staticmethod
    async def get_top_agent(conn):
        """
        Gets the top approved agent using the agent_scores materialized view.
        Uses only the maximum set_id and applies the 1.5% leadership rule.
        """
        from api.src.utils.models import TopAgentHotkey
        
        # Get the maximum set_id
        max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
        if not max_set_id_result or max_set_id_result['max_set_id'] is None:
            return None
        
        max_set_id = max_set_id_result['max_set_id']
        
        # Get current leader from materialized view
        current_leader = await conn.fetchrow("""
            SELECT ass.version_id, ass.miner_hotkey, ass.final_score, ass.created_at
            FROM agent_scores ass
            WHERE ass.approved = true AND ass.set_id = $1
            ORDER BY ass.final_score DESC, ass.created_at ASC
            LIMIT 1
        """, max_set_id)
        
        if not current_leader:
            return None
        
        current_leader_score = current_leader['final_score']
        required_score = current_leader_score * 1.015  # Must beat by 1.5%
        
        # Find challenger that beats current leader by 1.5%
        challenger = await conn.fetchrow("""
            SELECT ass.version_id, ass.miner_hotkey, ass.final_score
            FROM agent_scores ass
            WHERE ass.approved = true AND ass.set_id = $2 AND ass.final_score >= $1
            ORDER BY ass.final_score DESC, ass.created_at ASC
            LIMIT 1
        """, required_score, max_set_id)
        
        # Return challenger if found, otherwise keep current leader
        winner = challenger if challenger else current_leader
        
        return TopAgentHotkey(
            miner_hotkey=winner['miner_hotkey'],
            version_id=winner['version_id'],
            avg_score=float(winner['final_score'])
        )
    
    @staticmethod
    async def get_top_agents(conn, num_agents: int = 3) -> list:
        """Get top agents using the agent_scores materialized view - returns ALL agents regardless of approval status"""
        # Get the maximum set_id
        max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
        if not max_set_id_result or max_set_id_result['max_set_id'] is None:
            return []
        
        max_set_id = max_set_id_result['max_set_id']
        
        results = await conn.fetch("""
            SELECT 
                version_id, miner_hotkey, agent_name, version_num,
                created_at, status, agent_summary, set_id,
                approved, validator_count, final_score as score
            FROM agent_scores
            WHERE set_id = $1
            ORDER BY final_score DESC, created_at ASC
            LIMIT $2
        """, max_set_id, num_agents)

        return [MinerAgentWithScores(**dict(row)) for row in results]
    
    @staticmethod
    async def get_24_hour_statistics(conn) -> dict:
        """Get 24-hour statistics using the agent_scores materialized view"""
        # Get current statistics based on computed scores from max set_id
        max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
        max_set_id = max_set_id_result['max_set_id'] if max_set_id_result else None
        
        if max_set_id is None:
            # No evaluation sets exist yet
            total_agents = await conn.fetchval("SELECT COUNT(*) FROM miner_agents")
            recent_agents = await conn.fetchval("SELECT COUNT(*) FROM miner_agents WHERE created_at >= NOW() - INTERVAL '24 hours'")
            
            return {
                'number_of_agents': total_agents or 0,
                'agent_iterations_last_24_hours': recent_agents or 0,
                'top_agent_score': None,
                'daily_score_improvement': 0
            }
        
        result = await conn.fetchrow("""
            WITH current_max AS (
                SELECT MAX(ass.final_score) as max_score
                FROM agent_scores ass
                WHERE ass.set_id = $1
            ),
            yesterday_max AS (
                SELECT MAX(ass.final_score) as max_score
                FROM agent_scores ass
                WHERE ass.set_id = $1
                AND ass.created_at < NOW() - INTERVAL '48 hours'
            )
            SELECT
                (SELECT COUNT(DISTINCT miner_hotkey) FROM miner_agents WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)) as number_of_agents,
                (SELECT COUNT(*) FROM miner_agents WHERE created_at >= NOW() - INTERVAL '48 hours' AND miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)) as agent_iterations_last_24_hours,
                (SELECT max_score FROM current_max) as top_agent_score,
                COALESCE((SELECT max_score FROM current_max) - COALESCE((SELECT max_score FROM yesterday_max), 0), 0) as daily_score_improvement
        """, max_set_id)
        
        if result is None:
            return {
                'number_of_agents': 0,
                'agent_iterations_last_24_hours': 0,
                'top_agent_score': None,
                'daily_score_improvement': 0
            }

        return {
            'number_of_agents': result['number_of_agents'],
            'agent_iterations_last_24_hours': result['agent_iterations_last_24_hours'],
            'top_agent_score': result['top_agent_score'],
            'daily_score_improvement': result['daily_score_improvement']
        }
    
    @staticmethod
    async def get_agent_summary_by_hotkey(conn, miner_hotkey: str) -> list:
        """Get agent summary by hotkey using the agent_scores materialized view where available"""
        results = await conn.fetch("""
            SELECT 
                ma.version_id, ma.miner_hotkey, ma.agent_name, ma.version_num,
                ma.created_at, ma.status, ma.agent_summary,
                COALESCE(ass.set_id, NULL) as set_id,
                COALESCE(ass.approved, NULL) as approved,
                COALESCE(ass.validator_count, NULL) as validator_count,
                COALESCE(ass.final_score, NULL) as score
            FROM miner_agents ma
            LEFT JOIN agent_scores ass ON ma.version_id = ass.version_id
            WHERE ma.miner_hotkey = $1
            AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            ORDER BY ma.version_num DESC, ma.created_at DESC
        """, miner_hotkey)
        
        return [MinerAgentWithScores(**dict(row)) for row in results]
    
    @staticmethod
    async def get_agents_with_scores_by_set_id(conn, num_agents: int = 10) -> list[dict]:
        """Get agents with their scores grouped by set_id using the materialized view"""
        results = await conn.fetch("""
            SELECT 
                version_id, miner_hotkey, agent_name, version_num,
                created_at, status, set_id, approved,
                validator_count as num_validators, final_score as computed_score
            FROM agent_scores
            ORDER BY set_id DESC, final_score DESC, created_at ASC
            LIMIT $1
        """, num_agents)

        return [dict(row) for row in results]
    
    @staticmethod
    async def refresh_materialized_view(conn):
        """Manually refresh the agent_scores materialized view"""
        await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
    
class AgentStatus(Enum):
    """States for miner agents - clear and unambiguous"""
    awaiting_screening = "awaiting_screening"          # Just uploaded, needs screening
    screening = "screening"                  # Currently being screened  
    failed_screening = "failed_screening"              # Failed screening (score < 0.8)
    waiting = "waiting"           # Passed screening, needs evaluation
    evaluating = "evaluating"               # Currently being evaluated
    scored = "scored"                       # All evaluations complete
    replaced = "replaced"                   # Replaced by newer version
    
    @classmethod
    def from_string(cls, status: str) -> 'AgentStatus':
        """Map database status string to agent state enum"""
        mapping = {
            "awaiting_screening": cls.awaiting_screening,
            "screening": cls.screening,
            "failed_screening": cls.failed_screening,
            "waiting": cls.waiting,
            "evaluating": cls.evaluating,
            "scored": cls.scored,
            "replaced": cls.replaced
        }
        return mapping.get(status, cls.awaiting_screening)


class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running"
    replaced = "replaced"
    error = "error"
    completed = "completed"
    cancelled = "cancelled"

    @classmethod
    def from_string(cls, status: str) -> 'EvaluationStatus':
        """Map database status string to evaluation state enum"""
        mapping = {
            "waiting": cls.waiting,
            "running": cls.running,
            "error": cls.error,
            "replaced": cls.replaced,
            "completed": cls.completed,
            "cancelled": cls.cancelled,
        }
        return mapping.get(status, cls.error)

class Evaluation(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    evaluation_id: UUID
    version_id: UUID
    validator_hotkey: str
    set_id: int
    status: EvaluationStatus
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    score: Optional[float]

class SandboxStatus(Enum):
    started = "started"
    sandbox_created = "sandbox_created"
    patch_generated = "patch_generated"
    eval_started = "eval_started"
    result_scored = "result_scored"
    cancelled = "cancelled"

class EvaluationRun(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True, 
        "validate_assignment": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    run_id: UUID
    evaluation_id: UUID
    swebench_instance_id: str
    response: Optional[str] = None
    error: Optional[str] = None
    pass_to_fail_success: Optional[str] = None
    fail_to_pass_success: Optional[str] = None
    pass_to_pass_success: Optional[str] = None
    fail_to_fail_success: Optional[str] = None
    solved: Optional[bool] = None
    status: SandboxStatus
    started_at: datetime
    sandbox_created_at: Optional[datetime] = None
    patch_generated_at: Optional[datetime] = None
    eval_started_at: Optional[datetime] = None
    result_scored_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

class EvaluationRunWithUsageDetails(EvaluationRun):
    cost: Optional[float]
    total_tokens: Optional[int]
    model: Optional[str]
    num_inference_calls: Optional[int]

class EvaluationsWithHydratedRuns(Evaluation):
    evaluation_runs: list[EvaluationRun]

class EvaluationsWithHydratedUsageRuns(Evaluation):
    evaluation_runs: list[EvaluationRunWithUsageDetails]

class EvaluationRunLog(BaseModel):
    id: UUID
    run_id: UUID
    created_at: datetime
    line: str

class Client(BaseModel):
    """Base class for connected clients"""
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }

    client_id: Optional[str] = None
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    websocket: Optional[WebSocket] = None

    def get_type(self) -> str:
        """Return the type of client"""
        return "client"

class Inference(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    id: UUID
    run_id: UUID
    message: str
    temperature: float
    model: str
    cost: float
    response: str
    total_tokens: int
    created_at: datetime
    finished_at: Optional[datetime]

class EvaluationQueueItem(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            UUID: str
        }
    }
    
    evaluation_id: UUID
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    created_at: datetime

class ValidatorQueueInfo(BaseModel):
    validator_hotkey: str
    queue_size: int
    queue: list[EvaluationQueueItem]