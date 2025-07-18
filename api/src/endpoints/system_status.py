"""
System Status Endpoint

Provides real-time status information about the evaluation system,
including health checks, recovery status, and system metrics.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
import logging

from api.src.backend.health_monitor import health_monitor
from api.src.backend.startup_recovery import recovery_manager
from api.src.backend.db_manager import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter()


class SystemStatusResponse(BaseModel):
    """Response model for system status"""
    status: str  # 'healthy', 'warning', 'critical', 'starting'
    message: str
    uptime_info: Dict
    health_checks: Dict
    recovery_status: Dict
    system_metrics: Dict


@router.get("/health", response_model=Dict)
async def get_health_status():
    """
    Get current health status of the evaluation system.
    Used for load balancer health checks and monitoring.
    """
    try:
        health_status = await health_monitor.get_health_status()
        
        # Simple health check response
        if health_status["status"] == "healthy":
            return {
                "status": "healthy",
                "message": "System is operating normally",
                "timestamp": health_status.get("last_check")
            }
        else:
            return {
                "status": health_status["status"],
                "message": health_status["message"],
                "timestamp": health_status.get("last_check")
            }
            
    except Exception as e:
        logger.error(f"Error getting health status: {e}")
        raise HTTPException(status_code=500, detail="Health check failed")


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status():
    """
    Get comprehensive system status including health, recovery, and metrics.
    """
    try:
        # Get health status
        health_status = await health_monitor.get_health_status()
        
        # Get recovery status
        recovery_status = await recovery_manager.get_recovery_status()
        
        # Get system metrics
        system_metrics = await _get_system_metrics()
        
        # Determine overall status
        overall_status = "healthy"
        message = "System is operating normally"
        
        if not recovery_status["completed"]:
            overall_status = "starting"
            message = "System is starting up and recovering"
        elif health_status["status"] == "critical":
            overall_status = "critical"
            message = "System has critical issues"
        elif health_status["status"] == "warning":
            overall_status = "warning"
            message = "System has warnings"
        
        return SystemStatusResponse(
            status=overall_status,
            message=message,
            uptime_info={
                "monitoring_active": health_status["is_monitoring"],
                "recovery_completed": recovery_status["completed"]
            },
            health_checks=health_status,
            recovery_status=recovery_status,
            system_metrics=system_metrics
        )
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail="System status check failed")


@router.post("/health-check", response_model=Dict)
async def run_manual_health_check():
    """
    Run a manual health check and return results.
    Useful for debugging and manual system verification.
    """
    try:
        results = await health_monitor.run_manual_health_check()
        return results
        
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
                    COUNT(*) FILTER (WHERE status = 'replaced') as replaced_agents
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
                    COUNT(*) FILTER (WHERE status = 'error' AND terminated_reason != 'timed out') as failed_evaluations,
                    COUNT(*) FILTER (WHERE status = 'error' AND terminated_reason = 'timed out') as timed_out_evaluations,
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


# Add routes to router
routes = [
    ("/health", get_health_status),
    ("/status", get_system_status),
    ("/health-check", run_manual_health_check),
]

for path, endpoint in routes:
    if path == "/health-check":
        router.add_api_route(path, endpoint, methods=["POST"])
    else:
        router.add_api_route(path, endpoint, methods=["GET"])