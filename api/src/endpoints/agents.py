from typing import Optional, Any, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.queries.agent import get_agent_by_agent_id
from api.src.backend.db_manager import db_operation
from api.src.backend.entities import Inference, EvaluationStatus
from api.src.backend.queries.agents import get_agent_approved_banned
from api.src.utils.auth import verify_request_public
from loggers.logging_utils import get_logger
from models.agent import AgentStatus, Agent
from models.evaluation_run import EvaluationRunStatus

logger = get_logger(__name__)

@db_operation
async def get_inferences_for_agent_version(conn: asyncpg.Connection, agent_id: str, set_id: Optional[int] = None, limit: int = 100) -> list[Inference]:
    """Get all inferences made by an agent version across all its evaluation runs"""
    if set_id is None:
        set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
    
    inferences = await conn.fetch(f"""
        SELECT DISTINCT
            i.id, i.evaluation_run_id, 
            (SELECT message->>'content' FROM jsonb_array_elements(i.messages) WITH ORDINALITY AS t(message, index) 
             WHERE message->>'role' = 'user' 
             ORDER BY index DESC LIMIT 1) as message, 
            i.temperature, i.model, i.cost, i.response, i.total_tokens, 
            i.created_at, i.finished_at, i.provider, i.status_code
        FROM inferences i
        JOIN evaluation_runs er ON i.evaluation_run_id = er.evaluation_run_id
        JOIN evaluations e ON er.evaluation_id = e.evaluation_id
        WHERE e.agent_id = $1
        AND e.set_id = $2
        AND er.status != '{EvaluationRunStatus.error.value}'
        ORDER BY i.created_at DESC
        LIMIT $3
    """, agent_id, set_id, limit)
    
    return [Inference(**dict(inference)) for inference in inferences]

@db_operation
async def get_inference_stats_for_agent_version(conn: asyncpg.Connection, agent_id: str, set_id: Optional[int] = None) -> Dict[str, Any]:
    """Get aggregated inference statistics for an agent version"""
    if set_id is None:
        set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
    
    stats = await conn.fetchrow(f"""
        SELECT 
            COUNT(i.id) as total_inferences,
            SUM(i.cost) as total_cost,
            SUM(i.total_tokens) as total_tokens,
            AVG(i.cost) as avg_cost_per_inference,
            AVG(i.total_tokens) as avg_tokens_per_inference,
            AVG(EXTRACT(EPOCH FROM (i.finished_at - i.created_at))) as avg_time_per_inference,
            COUNT(DISTINCT i.provider) as providers_used,
            COUNT(DISTINCT i.model) as models_used,
            COUNT(CASE WHEN i.status_code = 200 THEN 1 END) as successful_inferences,
            COUNT(CASE WHEN i.status_code != 200 THEN 1 END) as failed_inferences
        FROM inferences i
        JOIN evaluation_runs er ON i.evaluation_run_id = er.evaluation_run_id
        JOIN evaluations e ON er.evaluation_id = e.evaluation_id
        WHERE e.agent_id = $1
        AND e.set_id = $2
        AND er.status != '{EvaluationRunStatus.error.value}'
        AND i.finished_at IS NOT NULL
    """, agent_id, set_id)
    
    if stats is None:
        return {
            'total_inferences': 0,
            'total_cost': 0.0,
            'total_tokens': 0,
            'avg_cost_per_inference': 0.0,
            'avg_tokens_per_inference': 0.0,
            'avg_time_per_inference': 0.0,
            'providers_used': 0,
            'models_used': 0,
            'successful_inferences': 0,
            'failed_inferences': 0,
            'success_rate': 0.0,
            'error_rate': 0.0
        }
    
    result = dict(stats)
    total = int(result['total_inferences'])
    if total > 0:
        result['success_rate'] = (float(result['successful_inferences']) / total) * 100
        result['error_rate'] = (float(result['failed_inferences']) / total) * 100
    else:
        result['success_rate'] = 0.0
        result['error_rate'] = 0.0
    
    return result

@db_operation
async def get_progress_for_agent_version(conn: asyncpg.Connection, agent_id: str, set_id: Optional[int] = None) -> Dict[str, Any]:
    """Get progress information for all evaluations of an agent version"""
    if set_id is None:
        set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
    
    # Get evaluation progress
    evaluation_progress = await conn.fetch(f"""
        SELECT 
            e.evaluation_id,
            e.validator_hotkey,
            e.status as evaluation_status,
            e.score,
            COALESCE(AVG(
                CASE er.status
                    WHEN '{EvaluationRunStatus.pending.value}' THEN 0.0
                    WHEN '{EvaluationRunStatus.initializing_agent.value}' THEN 0.2
                    WHEN '{EvaluationRunStatus.running_agent.value}' THEN 0.4
                    WHEN '{EvaluationRunStatus.initializing_eval.value}' THEN 0.6
                    WHEN '{EvaluationRunStatus.running_eval.value}' THEN 0.8
                    WHEN '{EvaluationRunStatus.finished.value}' THEN 1.0
                    ELSE 0.0
                END
            ), 0.0) as progress
        FROM evaluations_hydrated e
        LEFT JOIN evaluation_runs er ON e.evaluation_id = er.evaluation_id 
            AND er.status NOT IN ('{EvaluationRunStatus.error.value}')
        WHERE e.agent_id = $1
        AND e.set_id = $2
        GROUP BY e.evaluation_id, e.validator_hotkey, e.status, e.score, e.created_at
        ORDER BY e.created_at DESC
    """, agent_id, set_id)
    
    # Get overall statistics
    running_states = [EvaluationRunStatus.initializing_agent, EvaluationRunStatus.running_agent, EvaluationRunStatus.initializing_eval, EvaluationRunStatus.running_eval]
    running_states_str = " ".join((f"'{state.value}'" for state in running_states))

    overall_stats = await conn.fetchrow(f"""
        SELECT 
            COUNT(DISTINCT e.evaluation_id) as total_evaluations,
            COUNT(DISTINCT CASE WHEN e.status = '{EvaluationRunStatus.finished.value}' THEN e.evaluation_id END) as completed_evaluations,
            COUNT(DISTINCT CASE WHEN e.status IN ({running_states_str}) THEN e.evaluation_id END) as running_evaluations,
            COUNT(DISTINCT CASE WHEN e.status = '{EvaluationRunStatus.error.value}' THEN e.evaluation_id END) as failed_evaluations,
            AVG(e.score) FILTER (WHERE e.score IS NOT NULL) as avg_score,
            MAX(e.score) as max_score,
            COUNT(DISTINCT er.evaluation_run_id) as total_runs,
            COUNT(DISTINCT CASE WHEN er.status = '{EvaluationRunStatus.finished.value}' THEN er.evaluation_run_id END) as completed_runs
        FROM evaluations_hydrated e
        LEFT JOIN evaluation_runs er ON e.evaluation_id = er.evaluation_id
        WHERE e.agent_id = $1
        AND e.set_id = $2
    """, agent_id, set_id)
    
    evaluations = []
    for row in evaluation_progress:
        evaluations.append({
            'evaluation_id': str(row['evaluation_id']),
            'validator_hotkey': row['validator_hotkey'],
            'status': row['evaluation_status'],
            'score': row['score'],
            'progress': float(row['progress'])
        })
    
    overall_progress = 0.0
    if overall_stats['total_evaluations'] > 0:
        overall_progress = sum(eval['progress'] for eval in evaluations) / len(evaluations)
    
    return {
        'overall_progress': overall_progress,
        'total_evaluations': overall_stats['total_evaluations'] or 0,
        'completed_evaluations': overall_stats['completed_evaluations'] or 0,
        'running_evaluations': overall_stats['running_evaluations'] or 0,
        'failed_evaluations': overall_stats['failed_evaluations'] or 0,
        'avg_score': float(overall_stats['avg_score']) if overall_stats['avg_score'] else None,
        'max_score': float(overall_stats['max_score']) if overall_stats['max_score'] else None,
        'total_runs': overall_stats['total_runs'] or 0,
        'completed_runs': overall_stats['completed_runs'] or 0,
        'evaluations': evaluations
    }

async def get_agent_by_version(agent_id: str) -> Agent:
    """Get agent information by version ID"""
    agent = await get_agent_by_agent_id(agent_id=agent_id)
    if not agent:
        logger.info(f"Agent version {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    return agent

async def get_agent_inferences(agent_id: str, set_id: Optional[int] = None, limit: int = Query(default=100, le=1000)) -> list[Inference]:
    """Get inferences made by this agent version"""
    try:
        inferences = await get_inferences_for_agent_version(agent_id=agent_id, set_id=set_id, limit=limit)
        return inferences
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving inferences for agent version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving inferences. Please try again later."
        )

async def get_agent_inference_stats(agent_id: str, set_id: Optional[int] = None) -> Dict[str, Any]:
    """Get inference statistics for this agent version"""
    try:
        stats = await get_inference_stats_for_agent_version(agent_id=agent_id, set_id=set_id)
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving inference stats for agent version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving inference statistics. Please try again later."
        )

async def get_agent_progress(agent_id: str, set_id: Optional[int] = None) -> Dict[str, Any]:
    """Get progress information for this agent version"""
    try:
        progress = await get_progress_for_agent_version(agent_id=agent_id, set_id=set_id)
        return progress
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving progress for agent version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving progress. Please try again later."
        )

async def get_agent_status(agent_id: str) -> Dict[str, Any]:
    """Get miner agent status and metadata"""
    try:
        agent = await get_agent_by_version(agent_id)
        approved_at, banned = await get_agent_approved_banned(agent_id, agent.miner_hotkey)

        return {
            'agent_id': str(agent.agent_id),
            'miner_hotkey': agent.miner_hotkey,
            'agent_name': agent.name,
            'version_num': agent.version_num,
            'created_at': agent.created_at,
            'status': agent.status,
            'agent_summary': agent.agent_summary,
            'ip_address': agent.ip_address,
            'approved_at': approved_at,
            'banned': banned,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving status for agent version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent status. Please try again later."
        )

router = APIRouter()

@db_operation
async def get_flow_data_for_agent(conn: asyncpg.Connection, agent_id: str) -> Optional[Dict[str, Any]]:
    """Get flow data directly from database"""
    agent = await conn.fetchrow("""
        SELECT agent_id, miner_hotkey, name, version_num, created_at, status
        FROM agents WHERE agent_id = $1
    """, agent_id)
    
    if not agent:
        return None
    
    evaluations = await conn.fetch("""
        SELECT 
            e.evaluation_id, e.validator_hotkey, e.status, e.score,
            e.created_at, e.finished_at,
            COUNT(er.evaluation_run_id) as total_runs,
            COUNT(CASE WHEN er.status = 'result_scored' THEN 1 END) as completed_runs
        FROM evaluations_hydrated e
        LEFT JOIN evaluation_runs er ON e.evaluation_id = er.evaluation_id AND er.status != 'cancelled'
        WHERE e.agent_id = $1
        GROUP BY e.evaluation_id, e.validator_hotkey, e.status, e.score,
                 e.created_at, e.finished_at
        ORDER BY e.created_at ASC
    """, agent_id)
    
    # Build stages based on agent status
    stages = _build_flow_stages(agent['status'], evaluations)
    progress = _calculate_flow_progress(stages)
    
    return {
        "agent_id": str(agent['agent_id']),
        "miner_hotkey": agent['miner_hotkey'],
        "agent_name": agent['name'],
        "version_num": agent['version_num'],
        "created_at": agent['created_at'].isoformat(),
        "current_status": agent['status'],
        "stages": stages,
        "progress": progress,
        "evaluations": [dict(e) for e in evaluations]
    }

def _build_flow_stages(agent_status: str, evaluations) -> list[Dict[str, Any]]:
    """Build simple stage data based on agent status"""
    validator_evals = [e for e in evaluations if not
                      (e['validator_hotkey'].startswith('screener-') or e['validator_hotkey'].startswith('i-0'))]

    # Map status to stage completion
    stage_map = {
        AgentStatus.screening_1.value: 1,
        AgentStatus.failed_screening_1.value: 1,
        AgentStatus.screening_2.value: 2,
        AgentStatus.failed_screening_2.value: 2,
        AgentStatus.evaluating.value: 3,
        AgentStatus.finished.value: 4,
        AgentStatus.cancelled.value: 4,
    }

    completed_stages = stage_map.get(agent_status, 0)

    stages = [
        {"stage": "upload", "label": "Upload", "status": "completed"},
        {"stage": "screening_1", "label": "Stage 1 Screening", "status": "completed" if completed_stages >= 1 else "pending"},
        {"stage": "screening_2", "label": "Stage 2 Screening", "status": "completed" if completed_stages >= 2 else "pending"},
        {"stage": "validation", "label": "Validation", "status": "completed" if completed_stages >= 3 else "pending"},
        {"stage": "complete", "label": "Complete", "status": "completed" if completed_stages >= 4 else "pending"}
    ]

    # Set current stage to active
    if agent_status == AgentStatus.screening_1.value:
        stages[1]["status"] = "active"
    elif agent_status == AgentStatus.screening_2.value:
        stages[2]["status"] = "active"
    elif agent_status == AgentStatus.evaluating.value:
        stages[3]["status"] = "active"

    if validator_evals:
        stages[3]["validators"] = len(validator_evals)
        stages[3]["completed_validators"] = sum(1 for e in validator_evals if e['status'] == 'completed')

    return stages

def _calculate_flow_progress(stages: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate simple progress"""
    completed = sum(1 for s in stages if s['status'] == 'completed')
    overall = (completed / len(stages)) * 100
    current_stage = next((s for s in stages if s['status'] == 'active'), None)
    
    return {
        "overall": round(overall, 1),
        "current_stage": current_stage['stage'] if current_stage else None,
        "is_complete": overall >= 100
    }

async def get_agent_flow_state(agent_id: str) -> Dict[str, Any]:
    """Get flow state for React stepper UI"""
    try:
        flow_data = await get_flow_data_for_agent(agent_id)
        if not flow_data:
            raise HTTPException(status_code=404, detail="Agent not found")
        return flow_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving flow for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@db_operation
async def get_validator_progress_for_agent(conn: asyncpg.Connection, agent_id: str) -> list[Dict[str, Any]]:
    """Get detailed progress information for each validator evaluating a specific agent version"""
    
    # Query to get validator evaluation details
    results = await conn.fetch("""
        SELECT 
            e.validator_hotkey,
            e.status,
            e.created_at,
            e.finished_at as completed_at,
            e.score
        FROM evaluations_hydrated e
        WHERE e.agent_id = $1 
            AND e.set_id IS NULL  -- Only main validator evaluations, not screening
            AND e.validator_hotkey NOT LIKE 'screener-%'  -- Exclude screener evaluations
        ORDER BY e.created_at ASC  -- Order by when evaluation was created
    """, agent_id)
    
    validators = []
    for row in results:
        # Determine status and progress based on evaluation state
        if row["status"] == "result_scored" and row["score"] is not None:
            status = "completed"
            progress = 1.0
        elif row["status"] == "eval_started":
            status = "running"
            progress = 0.8
        elif row["status"] == "patch_generated":
            status = "running"
            progress = 0.6
        elif row["status"] == "sandbox_created":
            status = "running"
            progress = 0.4
        elif row["status"] == "started":
            status = "running"
            progress = 0.2
        elif row["status"] == "failed":
            status = "failed"
            progress = 0.0
        else:
            status = "pending"
            progress = 0.0
        
        # Create a friendly validator name from the hotkey
        validator_name = f"Validator {row['validator_hotkey'][:8]}..."
        
        validator_info = {
            "validator_hotkey": row["validator_hotkey"],
            "validator_name": validator_name,
            "status": status,
            "progress": progress,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            "score": float(row["score"]) if row["score"] is not None else None
        }
        validators.append(validator_info)
    
    return validators

async def get_validator_progress(agent_id: str) -> list[Dict[str, Any]]:
    """Get detailed progress information for each validator evaluating a specific agent version"""
    try:
        validators = await get_validator_progress_for_agent(agent_id)
        return validators
    except Exception as e:
        logger.error(f"Error fetching validator progress for {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch validator progress")

@db_operation
async def get_agent_final_score_data(conn: asyncpg.Connection, agent_id: str) -> Dict[str, Any]:
    """Get the final score for a completed agent version using the materialized view"""
    
    # Query the agent_scores materialized view which contains precomputed final scores
    result = await conn.fetchrow("""
        SELECT 
            ass.agent_id,
            ass.miner_hotkey,
            ass.name,
            ass.version_num,
            ass.created_at,
            ass.status,
            ass.agent_summary,
            ass.set_id,
            ass.approved,
            ass.validator_count,
            ass.final_score as score
        FROM agent_scores ass
        WHERE ass.agent_id = $1
    """, agent_id)
    
    if not result:
        raise HTTPException(
            status_code=404, 
            detail=f"No final score found for agent version {agent_id}. Agent may not have enough validator evaluations (need 2+) or scores may not be computed yet."
        )
    
    # Get the latest completion timestamp from evaluations for this version
    completion_result = await conn.fetchrow(f"""
        SELECT MAX(e.finished_at) as completed_at
        FROM evaluations_hydrated e
        WHERE e.agent_id = $1 
            AND e.status = '{EvaluationStatus.completed}'
            AND e.score IS NOT NULL
    """, agent_id)
    
    return {
        "agent_id": str(result["agent_id"]),
        "miner_hotkey": result["miner_hotkey"],
        "agent_name": result["name"],
        "version_num": result["version_num"],
        "created_at": result["created_at"].isoformat(),
        "status": result["status"],
        "agent_summary": result["agent_summary"],
        "set_id": result["set_id"],
        "approved": result["approved"],
        "validator_count": result["validator_count"],
        "score": float(result["score"]),
        "completed_at": completion_result["completed_at"].isoformat() if completion_result and completion_result["completed_at"] else None
    }

async def get_agent_final_score(agent_id: str) -> Dict[str, Any]:
    """Get the final score for a completed agent version"""
    try:
        score_data = await get_agent_final_score_data(agent_id)
        return score_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching final score for {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch agent final score")

routes = [
    ("/{agent_id}", get_agent_status),
    ("/{agent_id}/inferences", get_agent_inferences),
    ("/{agent_id}/inference-stats", get_agent_inference_stats),
    ("/{agent_id}/progress", get_agent_progress),
    ("/{agent_id}/flow", get_agent_flow_state),
    ("/{agent_id}/validator-progress", get_validator_progress),
    ("/{agent_id}/final-score", get_agent_final_score),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["agents"],
        dependencies=[Depends(verify_request_public)],
        methods=["GET"]
    )
