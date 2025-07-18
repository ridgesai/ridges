"""
Health Monitor and Recovery System

This module provides continuous monitoring of the evaluation system,
automatic recovery from inconsistent states, and periodic cleanup.

It ensures the system remains healthy and consistent even after
crashes, restarts, or unexpected failures.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from api.src.backend.db_manager import get_db_connection
from api.src.backend.state_machine import state_machine

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a health check operation"""
    check_name: str
    status: str  # 'healthy', 'warning', 'critical'
    message: str
    issues_found: int = 0
    issues_fixed: int = 0
    metadata: Dict = None


class EvaluationHealthMonitor:
    """
    Monitors the evaluation system for consistency and health.
    Automatically recovers from inconsistent states.
    """
    
    def __init__(self):
        self.is_running = False
        self._stop_event = asyncio.Event()
        self._health_results: List[HealthCheckResult] = []
    
    async def start(self, check_interval: int = 60):
        """
        Start the health monitor with automatic recovery.
        
        Args:
            check_interval: How often to run health checks (seconds)
        """
        if self.is_running:
            logger.warning("Health monitor is already running")
            return
        
        self.is_running = True
        self._stop_event.clear()
        
        logger.info(f"Starting evaluation health monitor with {check_interval}s interval")
        
        while not self._stop_event.is_set():
            try:
                # Run all health checks
                await self._run_health_checks()
                
                # Wait for next check or stop signal
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=check_interval)
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    continue  # Run next check
                    
            except Exception as e:
                logger.error(f"Error in health monitor loop: {e}")
                await asyncio.sleep(10)  # Short backoff on error
        
        self.is_running = False
        logger.info("Health monitor stopped")
    
    async def stop(self):
        """Stop the health monitor"""
        if not self.is_running:
            return
        
        logger.info("Stopping health monitor")
        self._stop_event.set()
        
        # Wait for monitor to stop
        while self.is_running:
            await asyncio.sleep(0.1)
    
    async def _run_health_checks(self):
        """Run all health checks and recovery procedures"""
        logger.debug("Running health checks")
        
        try:
            # Check 1: Handle timeouts
            result = await self._check_and_handle_timeouts()
            self._health_results.append(result)
            
            # Check 2: Validate state consistency
            result = await self._check_state_consistency()
            self._health_results.append(result)
            
            # Check 3: Clean up orphaned evaluations
            result = await self._check_orphaned_evaluations()
            self._health_results.append(result)
            
            # Check 4: Validate agent version consistency
            result = await self._check_agent_version_consistency()
            self._health_results.append(result)
            
            # Check 5: Validate evaluation-agent relationships
            result = await self._check_evaluation_agent_relationships()
            self._health_results.append(result)
            
            # Keep only last 100 results to prevent memory growth
            if len(self._health_results) > 100:
                self._health_results = self._health_results[-100:]
            
            # Log summary
            critical_issues = sum(1 for r in self._health_results[-5:] if r.status == 'critical')
            if critical_issues > 0:
                logger.warning(f"Health check completed with {critical_issues} critical issues")
            else:
                logger.debug("Health check completed successfully")
                
        except Exception as e:
            logger.error(f"Error during health checks: {e}")
            self._health_results.append(HealthCheckResult(
                check_name="health_check_error",
                status="critical",
                message=f"Health check failed: {str(e)}"
            ))
    
    async def _check_and_handle_timeouts(self) -> HealthCheckResult:
        """Check for and handle timed out evaluations"""
        try:
            # Use state machine's timeout handler
            await state_machine.handle_timeouts()
            
            return HealthCheckResult(
                check_name="timeout_check",
                status="healthy",
                message="Timeout check completed successfully"
            )
            
        except Exception as e:
            logger.error(f"Error handling timeouts: {e}")
            return HealthCheckResult(
                check_name="timeout_check",
                status="critical",
                message=f"Timeout check failed: {str(e)}"
            )
    
    async def _check_state_consistency(self) -> HealthCheckResult:
        """Check for inconsistent states between agents and evaluations"""
        try:
            async with get_db_connection() as conn:
                # Find agents marked as evaluating but with no running evaluations
                inconsistent_agents = await conn.fetch("""
                    SELECT ma.version_id, ma.miner_hotkey, ma.status
                    FROM miner_agents ma
                    WHERE ma.status = 'evaluating'
                    AND NOT EXISTS (
                        SELECT 1 FROM evaluations e
                        WHERE e.version_id = ma.version_id
                        AND e.status = 'running'
                    )
                """)
                
                issues_fixed = 0
                for agent_row in inconsistent_agents:
                    version_id = agent_row['version_id']
                    
                    # Check if all evaluations are actually complete
                    still_waiting = await conn.fetchval("""
                        SELECT COUNT(*) FROM evaluations 
                        WHERE version_id = $1 AND status = 'waiting'
                    """, version_id)
                    
                    if still_waiting == 0:
                        # All evaluations are complete - mark agent as scored
                        await conn.execute("""
                            UPDATE miner_agents 
                            SET status = 'scored' 
                            WHERE version_id = $1
                        """, version_id)
                        issues_fixed += 1
                        logger.info(f"Fixed inconsistent agent {version_id} - marked as scored")
                    else:
                        # There are waiting evaluations - mark agent as waiting
                        await conn.execute("""
                            UPDATE miner_agents 
                            SET status = 'waiting' 
                            WHERE version_id = $1
                        """, version_id)
                        issues_fixed += 1
                        logger.info(f"Fixed inconsistent agent {version_id} - marked as waiting")
                
                # Find evaluations marked as running but agent is not evaluating
                inconsistent_evals = await conn.fetch("""
                    SELECT e.evaluation_id, e.version_id, ma.status as agent_status
                    FROM evaluations e
                    JOIN miner_agents ma ON e.version_id = ma.version_id
                    WHERE e.status = 'running'
                    AND ma.status NOT IN ('evaluating', 'screening')
                """)
                
                for eval_row in inconsistent_evals:
                    evaluation_id = eval_row['evaluation_id']
                    # Reset evaluation to waiting
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'waiting', started_at = NULL
                        WHERE evaluation_id = $1
                    """, evaluation_id)
                    issues_fixed += 1
                    logger.info(f"Fixed inconsistent evaluation {evaluation_id} - reset to waiting")
                
                total_issues = len(inconsistent_agents) + len(inconsistent_evals)
                
                if total_issues == 0:
                    return HealthCheckResult(
                        check_name="state_consistency",
                        status="healthy",
                        message="No state inconsistencies found"
                    )
                else:
                    return HealthCheckResult(
                        check_name="state_consistency",
                        status="warning",
                        message=f"Fixed {issues_fixed} state inconsistencies",
                        issues_found=total_issues,
                        issues_fixed=issues_fixed
                    )
                    
        except Exception as e:
            logger.error(f"Error checking state consistency: {e}")
            return HealthCheckResult(
                check_name="state_consistency",
                status="critical",
                message=f"State consistency check failed: {str(e)}"
            )
    
    async def _check_orphaned_evaluations(self) -> HealthCheckResult:
        """Check for orphaned evaluations that should be cleaned up"""
        try:
            async with get_db_connection() as conn:
                now = datetime.now(timezone.utc)
                
                # Find evaluations that have been waiting too long (24 hours)
                stale_threshold = now - timedelta(hours=24)
                stale_evaluations = await conn.fetch("""
                    SELECT evaluation_id, version_id, validator_hotkey, created_at
                    FROM evaluations
                    WHERE status = 'waiting'
                    AND created_at < $1
                """, stale_threshold)
                
                issues_fixed = 0
                for eval_row in stale_evaluations:
                    evaluation_id = eval_row['evaluation_id']
                    
                    # Mark as timed out
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'error', terminated_reason = 'Timed out', finished_at = NOW()
                        WHERE evaluation_id = $1
                    """, evaluation_id)
                    issues_fixed += 1
                    logger.info(f"Cleaned up stale evaluation {evaluation_id}")
                
                # Find evaluations for replaced agents
                replaced_evaluations = await conn.fetch("""
                    SELECT e.evaluation_id, e.version_id
                    FROM evaluations e
                    JOIN miner_agents ma ON e.version_id = ma.version_id
                    WHERE e.status IN ('waiting', 'running')
                    AND ma.status = 'replaced'
                """)
                
                for eval_row in replaced_evaluations:
                    evaluation_id = eval_row['evaluation_id']
                    
                    # Mark as replaced
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'replaced', finished_at = NOW()
                        WHERE evaluation_id = $1
                    """, evaluation_id)
                    issues_fixed += 1
                    logger.info(f"Cleaned up evaluation for replaced agent {evaluation_id}")
                
                total_issues = len(stale_evaluations) + len(replaced_evaluations)
                
                if total_issues == 0:
                    return HealthCheckResult(
                        check_name="orphaned_evaluations",
                        status="healthy",
                        message="No orphaned evaluations found"
                    )
                else:
                    return HealthCheckResult(
                        check_name="orphaned_evaluations",
                        status="warning",
                        message=f"Cleaned up {issues_fixed} orphaned evaluations",
                        issues_found=total_issues,
                        issues_fixed=issues_fixed
                    )
                    
        except Exception as e:
            logger.error(f"Error checking orphaned evaluations: {e}")
            return HealthCheckResult(
                check_name="orphaned_evaluations",
                status="critical",
                message=f"Orphaned evaluations check failed: {str(e)}"
            )
    
    async def _check_agent_version_consistency(self) -> HealthCheckResult:
        """Check that only the latest version of each agent can be evaluated"""
        try:
            async with get_db_connection() as conn:
                # Find agents that aren't the latest version but have active evaluations
                inconsistent_versions = await conn.fetch("""
                    SELECT DISTINCT ma.version_id, ma.miner_hotkey, ma.version_num
                    FROM miner_agents ma
                    JOIN evaluations e ON ma.version_id = e.version_id
                    WHERE e.status IN ('waiting', 'running')
                    AND ma.version_num < (
                        SELECT MAX(ma2.version_num)
                        FROM miner_agents ma2
                        WHERE ma2.miner_hotkey = ma.miner_hotkey
                    )
                """)
                
                issues_fixed = 0
                for version_row in inconsistent_versions:
                    version_id = version_row['version_id']
                    
                    # Replace all evaluations for this old version
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'replaced', finished_at = NOW()
                        WHERE version_id = $1 AND status IN ('waiting', 'running')
                    """, version_id)
                    
                    # Mark agent as replaced
                    await conn.execute("""
                        UPDATE miner_agents 
                        SET status = 'replaced'
                        WHERE version_id = $1
                    """, version_id)
                    
                    issues_fixed += 1
                    logger.info(f"Fixed version consistency for agent {version_id}")
                
                total_issues = len(inconsistent_versions)
                
                if total_issues == 0:
                    return HealthCheckResult(
                        check_name="agent_version_consistency",
                        status="healthy",
                        message="All agent versions are consistent"
                    )
                else:
                    return HealthCheckResult(
                        check_name="agent_version_consistency",
                        status="warning",
                        message=f"Fixed {issues_fixed} version inconsistencies",
                        issues_found=total_issues,
                        issues_fixed=issues_fixed
                    )
                    
        except Exception as e:
            logger.error(f"Error checking agent version consistency: {e}")
            return HealthCheckResult(
                check_name="agent_version_consistency",
                status="critical",
                message=f"Agent version consistency check failed: {str(e)}"
            )
    
    async def _check_evaluation_agent_relationships(self) -> HealthCheckResult:
        """Check that evaluation-agent relationships are valid"""
        try:
            async with get_db_connection() as conn:
                # Find evaluations pointing to non-existent agents
                orphaned_evaluations = await conn.fetch("""
                    SELECT e.evaluation_id, e.version_id
                    FROM evaluations e
                    LEFT JOIN miner_agents ma ON e.version_id = ma.version_id
                    WHERE ma.version_id IS NULL
                    AND e.status IN ('waiting', 'running')
                """)
                
                issues_fixed = 0
                for eval_row in orphaned_evaluations:
                    evaluation_id = eval_row['evaluation_id']
                    
                    # Mark as error since agent doesn't exist
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'error', finished_at = NOW()
                        WHERE evaluation_id = $1
                    """, evaluation_id)
                    
                    issues_fixed += 1
                    logger.info(f"Fixed orphaned evaluation {evaluation_id} - agent doesn't exist")
                
                total_issues = len(orphaned_evaluations)
                
                if total_issues == 0:
                    return HealthCheckResult(
                        check_name="evaluation_agent_relationships",
                        status="healthy",
                        message="All evaluation-agent relationships are valid"
                    )
                else:
                    return HealthCheckResult(
                        check_name="evaluation_agent_relationships",
                        status="warning",
                        message=f"Fixed {issues_fixed} invalid relationships",
                        issues_found=total_issues,
                        issues_fixed=issues_fixed
                    )
                    
        except Exception as e:
            logger.error(f"Error checking evaluation-agent relationships: {e}")
            return HealthCheckResult(
                check_name="evaluation_agent_relationships",
                status="critical",
                message=f"Relationship check failed: {str(e)}"
            )
    
    async def get_health_status(self) -> Dict:
        """Get current health status of the system"""
        if not self._health_results:
            return {
                "status": "unknown",
                "message": "No health checks have been run yet",
                "last_check": None,
                "checks": []
            }
        
        # Get recent results (last 5 checks per type)
        recent_results = {}
        for result in reversed(self._health_results[-25:]):  # Last 25 results
            if result.check_name not in recent_results:
                recent_results[result.check_name] = result
        
        # Determine overall status
        statuses = [r.status for r in recent_results.values()]
        if 'critical' in statuses:
            overall_status = 'critical'
        elif 'warning' in statuses:
            overall_status = 'warning'
        else:
            overall_status = 'healthy'
        
        return {
            "status": overall_status,
            "message": f"System is {overall_status}",
            "last_check": self._health_results[-1].check_name if self._health_results else None,
            "is_monitoring": self.is_running,
            "checks": [
                {
                    "name": result.check_name,
                    "status": result.status,
                    "message": result.message,
                    "issues_found": result.issues_found,
                    "issues_fixed": result.issues_fixed
                }
                for result in recent_results.values()
            ]
        }
    
    async def run_manual_health_check(self) -> Dict:
        """Run a manual health check and return results"""
        logger.info("Running manual health check")
        await self._run_health_checks()
        return await self.get_health_status()


# Global health monitor instance
health_monitor = EvaluationHealthMonitor()