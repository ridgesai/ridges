## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime, timezone
from uuid import UUID

from fastapi import WebSocket
from pydantic import BaseModel, Field, EmailStr
from typing import Literal, Optional, TYPE_CHECKING
from enum import Enum



class MinerAgent(BaseModel): 
    """Maps to the agent_versions table"""
    model_config = { "arbitrary_types_allowed": True }
    
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str
    agent_summary: Optional[str] = None
    ip_address: Optional[str] = None
    innovation_score: Optional[float] = None

class AgentWithHydratedCode(MinerAgent):
    code: str

class MinerAgentWithScores(MinerAgent):
    """MinerAgent with computed scores by set_id"""
    score: Optional[float]
    set_id: Optional[int]
    approved: Optional[bool] = None

class MinerAgentScored(BaseModel):
    """Maps to the agent_scores materialized view with precomputed scoring logic"""
    model_config = { "arbitrary_types_allowed": True }
    
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str
    agent_summary: Optional[str] = None
    set_id: int
    approved: bool
    approved_at: Optional[datetime] = None
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
            WHERE ass.approved = true AND ass.approved_at <= NOW() AND ass.set_id = $1
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
            WHERE ass.approved = true AND ass.approved_at <= NOW() AND ass.set_id = $1
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
            WHERE ass.approved = true AND ass.approved_at <= NOW() AND ass.set_id = $2 AND ass.final_score >= $1
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
    async def get_top_agents(conn, num_agents: int = 3, search_term: Optional[str] = None, filter_for_open_user: bool = False, filter_for_registered_user: bool = False, filter_for_approved: bool = False) -> list:

        max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
        if not max_set_id_result or max_set_id_result['max_set_id'] is None:
            return []
        
        max_set_id = max_set_id_result['max_set_id']

        max_approved_set_id: Optional[int] = None
        if filter_for_approved:
            max_approved_set_id = await conn.fetchval("SELECT MAX(set_id) FROM approved_version_ids WHERE approved_at <= NOW()")
            if max_approved_set_id is None:
                return []

        where_clauses: list[str] = ["set_id = $1"]
        params: list = [max_set_id]

        if search_term is not None and len(search_term) > 0:
            param_idx = len(params) + 1
            like_param = f"%{search_term}%"
            where_clauses.append(
                f"(CAST(version_id AS TEXT) ILIKE ${param_idx} OR agent_name ILIKE ${param_idx} OR miner_hotkey ILIKE ${param_idx})"
            )
            params.append(like_param)

        if filter_for_open_user:
            where_clauses.append("miner_hotkey LIKE 'open-%'")

        if filter_for_registered_user:
            where_clauses.append("miner_hotkey NOT LIKE 'open-%'")

        if filter_for_approved and max_approved_set_id is not None:
            param_idx = len(params) + 1
            where_clauses.append(
                f"version_id IN (SELECT version_id FROM approved_version_ids WHERE set_id = ${param_idx} AND approved_at <= NOW())"
            )
            params.append(max_approved_set_id)

        query = f"""
            SELECT 
                version_id, miner_hotkey, agent_name, version_num,
                created_at, status, agent_summary, set_id,
                approved, validator_count, final_score as score
            FROM agent_scores
            WHERE {' AND '.join(where_clauses)}
            ORDER BY final_score DESC, created_at ASC
            LIMIT ${len(params) + 1}
        """

        results = await conn.fetch(query, *params, num_agents)

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
    """States for miner agents - multi-stage screening flow"""
    awaiting_screening_1 = "awaiting_screening_1"      # Just uploaded, needs stage 1 screening
    screening_1 = "screening_1"                        # Currently being screened by stage 1
    failed_screening_1 = "failed_screening_1"          # Failed stage 1 screening (score < 0.8)
    awaiting_screening_2 = "awaiting_screening_2"      # Passed stage 1, needs stage 2 screening
    screening_2 = "screening_2"                        # Currently being screened by stage 2
    failed_screening_2 = "failed_screening_2"          # Failed stage 2 screening (score < 0.8)
    waiting = "waiting"                                # Passed all screening, needs evaluation
    evaluating = "evaluating"                          # Currently being evaluated
    scored = "scored"                                  # All evaluations complete
    replaced = "replaced"                              # Replaced by newer version
    pruned = "pruned"                                  # Pruned due to low score compared to top agent
    
    # Legacy statuses for backward compatibility during transition
    awaiting_screening = "awaiting_screening_1"        # Map to stage 1
    screening = "screening_1"                          # Map to stage 1
    failed_screening = "failed_screening_1"            # Map to stage 1 fail
    evaluation = "evaluating"                          # Map to evaluating (legacy alias)
    
    @classmethod
    def from_string(cls, status: str) -> 'AgentStatus':
        """Map database status string to agent state enum"""
        mapping = {
            "awaiting_screening_1": cls.awaiting_screening_1,
            "screening_1": cls.screening_1,
            "failed_screening_1": cls.failed_screening_1,
            "awaiting_screening_2": cls.awaiting_screening_2,
            "screening_2": cls.screening_2,
            "failed_screening_2": cls.failed_screening_2,
            "waiting": cls.waiting,
            "evaluating": cls.evaluating,
            "scored": cls.scored,
            "replaced": cls.replaced,
            "pruned": cls.pruned,
            # Legacy mappings for backward compatibility
            "awaiting_screening": cls.awaiting_screening_1,
            "screening": cls.screening_1,
            "failed_screening": cls.failed_screening_1,
            "evaluation": cls.evaluating  # Critical: existing agents might have this status
        }
        return mapping.get(status, cls.awaiting_screening_1)


class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running"
    replaced = "replaced"
    error = "error"
    completed = "completed"
    cancelled = "cancelled"
    pruned = "pruned"

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
            "pruned": cls.pruned,
        }
        return mapping.get(status, cls.error)

class Evaluation(BaseModel):
    model_config = { "arbitrary_types_allowed": True }
    
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
    screener_score: Optional[float]

class SandboxStatus(Enum):
    started = "started"
    sandbox_created = "sandbox_created"
    patch_generated = "patch_generated"
    eval_started = "eval_started"
    result_scored = "result_scored"
    cancelled = "cancelled"

class EvaluationRun(BaseModel):
    model_config = { "arbitrary_types_allowed": True, "validate_assignment": True }
    
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
    logs: Optional[str] = None

class EvaluationRunWithUsageDetails(EvaluationRun):
    cost: Optional[float]
    total_tokens: Optional[int]
    model: Optional[str]
    num_inference_calls: Optional[int]

class EvaluationsWithHydratedRuns(Evaluation):
    evaluation_runs: list[EvaluationRun]

class EvaluationsWithHydratedUsageRuns(Evaluation):
    evaluation_runs: list[EvaluationRunWithUsageDetails]


class Client(BaseModel):
    """Base class for connected clients"""
    model_config = { "arbitrary_types_allowed": True }

    client_id: Optional[str] = None
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    websocket: Optional[WebSocket] = None
    status_mismatch_count: int = 0  # Track consecutive status mismatches

    def get_type(self) -> str:
        """Return the type of client"""
        return "client"
    
    def update_system_metrics(self, cpu_percent: Optional[float], ram_percent: Optional[float], 
                            disk_percent: Optional[float], containers: Optional[int],
                            ram_total_gb: Optional[float] = None, disk_total_gb: Optional[float] = None) -> None:
        """Update system metrics for this client - implemented by subclasses"""
        pass

class Inference(BaseModel):
    model_config = { "arbitrary_types_allowed": True }
    
    id: UUID
    run_id: UUID
    message: str
    temperature: float
    model: str
    cost: Optional[float] = None
    response: Optional[str] = None
    total_tokens: Optional[int] = None
    created_at: datetime
    finished_at: Optional[datetime]
    provider: Optional[str] = None
    status_code: Optional[int] = None

class InferenceSummary(BaseModel):
    """Simplified inference model with only essential fields"""
    temperature: Optional[float] = None
    model: Optional[str] = None
    cost: Optional[float] = None
    total_tokens: Optional[int] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    provider: Optional[str] = None
    status_code: Optional[int] = None

class ProviderStatistics(BaseModel):
    """Summary statistics by provider for inference performance"""
    provider: str
    avg_time_taken: Optional[float] = None
    median_time_taken: Optional[float] = None
    p95_time_taken: Optional[float] = None
    max_time_taken: Optional[float] = None
    min_time_taken: Optional[float] = None
    error_rate: Optional[float] = None
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_tokens: Optional[int] = None

class EvaluationQueueItem(BaseModel):
    model_config = { "arbitrary_types_allowed": True }
    
    evaluation_id: UUID
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    created_at: datetime
    screener_score: Optional[float]

class ValidatorQueueInfo(BaseModel):
    validator_hotkey: str
    queue_size: int
    queue: list[EvaluationQueueItem]


class ScreenerQueueAgent(BaseModel):
    """Agent in screener queue"""
    model_config = { "arbitrary_types_allowed": True }
    
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str

class ScreenerQueueByStage(BaseModel):
    """Screener queue organized by stage"""
    stage_1: list[ScreenerQueueAgent]
    stage_2: list[ScreenerQueueAgent]

class OpenUser(BaseModel):
    open_hotkey: str
    auth0_user_id: str
    email: str
    name: str
    registered_at: datetime
    agents: Optional[list[MinerAgent]] = []
    bittensor_hotkey: Optional[str] = None
    admin: Optional[int] = 7

class OpenUserSignInRequest(BaseModel):
    auth0_user_id: str
    email: EmailStr
    name: str
    password: str

class TreasuryTransaction(BaseModel):
    group_transaction_id: UUID
    sender_coldkey: str
    destination_coldkey: str
    staker_hotkey: str
    amount_alpha: int
    occurred_at: datetime
    version_id: UUID
    extrinsic_code: str
    fee: bool

class QuestionSolveRateStats(BaseModel):
    swebench_instance_id: str
    solved_percentage: float
    total_runs: int
    solved_runs: int
    not_solved_runs: int