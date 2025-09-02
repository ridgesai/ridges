"""
System Status Endpoint

Provides real-time status information about the evaluation system.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, List, Optional
import logging

from api.src.backend.db_manager import get_db_connection
from api.src.utils.auth import verify_request_public

logger = logging.getLogger(__name__)

router = APIRouter()


class SystemStatusResponse(BaseModel):
    """Response model for system status"""
    status: str  # 'healthy', 'warning', 'critical'
    message: str
    system_metrics: Dict


@router.get("/health", response_model=Dict, dependencies=[Depends(verify_request_public)])
async def get_health_status():
    """
    Get current health status of the evaluation system.
    Used for load balancer health checks and monitoring.
    """
    try:
        # Simple health check - verify database connectivity
        async with get_db_connection() as conn:
            await conn.fetchval("SELECT 1")
        
        return {
            "status": "healthy",
            "message": "System is operating normally"
        }
            
    except Exception as e:
        logger.error(f"Error getting health status: {e}")
        raise HTTPException(status_code=500, detail="Health check failed")


@router.get("/status", response_model=SystemStatusResponse, dependencies=[Depends(verify_request_public)])
async def get_system_status():
    """
    Get comprehensive system status including metrics.
    """
    try:
        # Get system metrics
        system_metrics = await _get_system_metrics()
        
        # Determine overall status based on running evaluations
        overall_status = "healthy"
        message = "System is operating normally"
        
        # Check for any obvious issues
        if system_metrics.get("evaluations", {}).get("running_evaluations", 0) > 100:
            overall_status = "warning"
            message = "High number of running evaluations"
        
        return SystemStatusResponse(
            status=overall_status,
            message=message,
            system_metrics=system_metrics
        )
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail="System status check failed")


@router.post("/health-check", response_model=Dict, dependencies=[Depends(verify_request_public)])
async def run_manual_health_check():
    """
    Run a manual health check and return results.
    Useful for debugging and manual system verification.
    """
    try:
        # Simple health check - verify database connectivity and basic metrics
        async with get_db_connection() as conn:
            await conn.fetchval("SELECT 1")
            
            # Check for obviously broken states
            running_evals = await conn.fetchval("SELECT COUNT(*) FROM evaluations WHERE status = 'running'")
            inconsistent_agents = await conn.fetchval("""
                SELECT COUNT(*) FROM miner_agents 
                WHERE status = 'evaluating' 
                AND NOT EXISTS (
                    SELECT 1 FROM evaluations 
                    WHERE version_id = miner_agents.version_id 
                    AND status = 'running'
                )
            """)
            
            issues = []
            if running_evals > 0:
                issues.append(f"{running_evals} evaluations still running after startup")
            if inconsistent_agents > 0:
                issues.append(f"{inconsistent_agents} agents in inconsistent state")
            
            return {
                "status": "healthy" if not issues else "warning",
                "message": "Manual health check completed",
                "issues": issues,
                "running_evaluations": running_evals,
                "inconsistent_agents": inconsistent_agents
            }
        
    except Exception as e:
        logger.error(f"Error running manual health check: {e}")
        raise HTTPException(status_code=500, detail="Manual health check failed")


async def _get_system_metrics() -> Dict:
    """Get system metrics from the database"""
    try:
        async with get_db_connection() as conn:
            # Agent metrics
            agent_metrics = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_agents,
                    COUNT(*) FILTER (WHERE status = 'screening') as screening_agents,
                    COUNT(*) FILTER (WHERE status = 'waiting') as waiting_agents,
                    COUNT(*) FILTER (WHERE status = 'evaluating') as evaluating_agents,
                    COUNT(*) FILTER (WHERE status = 'scored') as scored_agents,
                    COUNT(*) FILTER (WHERE status = 'replaced') as replaced_agents,
                    COUNT(*) FILTER (WHERE status = 'pruned') as pruned_agents
                FROM miner_agents
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            
            # Evaluation metrics
            eval_metrics = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_evaluations,
                    COUNT(*) FILTER (WHERE status = 'waiting') as waiting_evaluations,
                    COUNT(*) FILTER (WHERE status = 'running') as running_evaluations,
                    COUNT(*) FILTER (WHERE status = 'completed') as completed_evaluations,
                    COUNT(*) FILTER (WHERE status = 'pruned') as pruned_evaluations,
                    COUNT(*) FILTER (WHERE status = 'error') as failed_evaluations,
                    COUNT(*) FILTER (WHERE status = 'replaced') as replaced_evaluations
                FROM evaluations
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            
            # Recent activity metrics
            recent_activity = await conn.fetchrow("""
                SELECT 
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as agents_last_hour,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as evaluations_last_hour
                FROM miner_agents ma
                FULL OUTER JOIN evaluations e ON ma.version_id = e.version_id
            """)
            
            return {
                "agents": dict(agent_metrics) if agent_metrics else {},
                "evaluations": dict(eval_metrics) if eval_metrics else {},
                "recent_activity": dict(recent_activity) if recent_activity else {}
            }
            
    except Exception as e:
        logger.error(f"Error getting system metrics: {e}")
        return {
            "agents": {},
            "evaluations": {},
            "recent_activity": {},
            "error": str(e)
        }


