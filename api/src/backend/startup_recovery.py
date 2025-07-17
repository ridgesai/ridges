"""
Startup Recovery System

This module handles system recovery after restarts, crashes, or deployments.
It ensures the system starts in a consistent state and handles any
incomplete operations from the previous session.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from api.src.backend.db_manager import get_db_connection
from api.src.backend.state_machine import state_machine
from api.src.backend.health_monitor import health_monitor

logger = logging.getLogger(__name__)


class StartupRecoveryManager:
    """
    Manages system recovery during startup.
    Ensures the system starts in a consistent, healthy state.
    """
    
    def __init__(self):
        self.recovery_completed = False
        self.recovery_results: Dict = {}
    
    async def perform_startup_recovery(self) -> Dict:
        """
        Perform complete startup recovery.
        This should be called once when the system starts up.
        """
        if self.recovery_completed:
            logger.warning("Startup recovery already completed")
            return self.recovery_results
        
        logger.info("Starting system recovery after startup")
        
        recovery_start = datetime.now(timezone.utc)
        results = {
            "started_at": recovery_start,
            "completed_at": None,
            "success": False,
            "steps": []
        }
        
        try:
            # Step 1: Reset all running evaluations
            step_result = await self._reset_running_evaluations()
            results["steps"].append(step_result)
            
            # Step 2: Clean up inconsistent agent states
            step_result = await self._clean_agent_states()
            results["steps"].append(step_result)
            
            # Step 3: Handle abandoned screening evaluations
            step_result = await self._handle_abandoned_screenings()
            results["steps"].append(step_result)
            
            # Step 4: Timeout old evaluations
            step_result = await self._timeout_old_evaluations()
            results["steps"].append(step_result)
            
            # Step 5: Validate database consistency
            step_result = await self._validate_database_consistency()
            results["steps"].append(step_result)
            
            # Step 6: Start health monitoring
            step_result = await self._start_health_monitoring()
            results["steps"].append(step_result)
            
            results["completed_at"] = datetime.now(timezone.utc)
            results["success"] = True
            
            duration = (results["completed_at"] - recovery_start).total_seconds()
            logger.info(f"Startup recovery completed successfully in {duration:.2f}s")
            
            self.recovery_completed = True
            self.recovery_results = results
            
            return results
            
        except Exception as e:
            logger.error(f"Startup recovery failed: {e}")
            results["completed_at"] = datetime.now(timezone.utc)
            results["success"] = False
            results["error"] = str(e)
            
            self.recovery_results = results
            return results
    
    async def _reset_running_evaluations(self) -> Dict:
        """Reset all evaluations that were running when the system shut down"""
        step_name = "reset_running_evaluations"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            async with get_db_connection() as conn:
                # Find all evaluations that were running
                running_evaluations = await conn.fetch("""
                    SELECT evaluation_id, version_id, validator_hotkey, started_at
                    FROM evaluations
                    WHERE status = 'running'
                """)
                
                reset_count = 0
                for eval_row in running_evaluations:
                    evaluation_id = eval_row['evaluation_id']
                    validator_hotkey = eval_row['validator_hotkey']
                    
                    # Reset to waiting state
                    await conn.execute("""
                        UPDATE evaluations 
                        SET status = 'waiting', started_at = NULL
                        WHERE evaluation_id = $1
                    """, evaluation_id)
                    
                    reset_count += 1
                    logger.info(f"Reset evaluation {evaluation_id} from validator {validator_hotkey}")
                
                # Reset agent states that were evaluating
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = 'waiting'
                    WHERE status = 'evaluating'
                    AND EXISTS (
                        SELECT 1 FROM evaluations 
                        WHERE evaluations.version_id = miner_agents.version_id
                        AND evaluations.status = 'waiting'
                    )
                """)
                
                # Reset agent states that were screening
                await conn.execute("""
                    UPDATE miner_agents 
                    SET status = 'screening'
                    WHERE status = 'screening'
                    AND EXISTS (
                        SELECT 1 FROM evaluations 
                        WHERE evaluations.version_id = miner_agents.version_id
                        AND evaluations.status = 'waiting'
                        AND evaluations.validator_hotkey LIKE 'i-0%'
                    )
                """)
                
                return {
                    "step": step_name,
                    "success": True,
                    "message": f"Reset {reset_count} running evaluations",
                    "evaluations_reset": reset_count
                }
                
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def _clean_agent_states(self) -> Dict:
        """Clean up inconsistent agent states"""
        step_name = "clean_agent_states"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            async with get_db_connection() as conn:
                # Find agents marked as evaluating but no running evaluations
                inconsistent_agents = await conn.fetch("""
                    SELECT version_id, miner_hotkey, status
                    FROM miner_agents
                    WHERE status = 'evaluating'
                    AND NOT EXISTS (
                        SELECT 1 FROM evaluations
                        WHERE evaluations.version_id = miner_agents.version_id
                        AND evaluations.status = 'running'
                    )
                """)
                
                fixed_count = 0
                for agent_row in inconsistent_agents:
                    version_id = agent_row['version_id']
                    
                    # Check if there are waiting evaluations
                    waiting_count = await conn.fetchval("""
                        SELECT COUNT(*) FROM evaluations
                        WHERE version_id = $1 AND status = 'waiting'
                    """, version_id)
                    
                    if waiting_count > 0:
                        # Mark as waiting
                        await conn.execute("""
                            UPDATE miner_agents 
                            SET status = 'waiting'
                            WHERE version_id = $1
                        """, version_id)
                    else:
                        # Mark as scored
                        await conn.execute("""
                            UPDATE miner_agents 
                            SET status = 'scored'
                            WHERE version_id = $1
                        """, version_id)
                    
                    fixed_count += 1
                    logger.info(f"Fixed inconsistent agent state for {version_id}")
                
                return {
                    "step": step_name,
                    "success": True,
                    "message": f"Fixed {fixed_count} inconsistent agent states",
                    "agents_fixed": fixed_count
                }
                
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def _handle_abandoned_screenings(self) -> Dict:
        """Handle screening evaluations that were abandoned"""
        step_name = "handle_abandoned_screenings"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            async with get_db_connection() as conn:
                # Find screening evaluations that are waiting
                screening_evaluations = await conn.fetch("""
                    SELECT evaluation_id, version_id, validator_hotkey
                    FROM evaluations
                    WHERE status = 'waiting'
                    AND validator_hotkey LIKE 'i-0%'
                """)
                
                # Reset associated agents to pending screening
                handled_count = 0
                for eval_row in screening_evaluations:
                    version_id = eval_row['version_id']
                    
                    # Make sure agent is in correct state
                    await conn.execute("""
                        UPDATE miner_agents 
                        SET status = 'screening'
                        WHERE version_id = $1
                    """, version_id)
                    
                    handled_count += 1
                    logger.info(f"Handled abandoned screening for agent {version_id}")
                
                return {
                    "step": step_name,
                    "success": True,
                    "message": f"Handled {handled_count} abandoned screenings",
                    "screenings_handled": handled_count
                }
                
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def _timeout_old_evaluations(self) -> Dict:
        """Timeout evaluations that are too old"""
        step_name = "timeout_old_evaluations"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            # Use state machine's timeout handler
            await state_machine.handle_timeouts()
            
            return {
                "step": step_name,
                "success": True,
                "message": "Handled old evaluation timeouts"
            }
            
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def _validate_database_consistency(self) -> Dict:
        """Validate that database is in a consistent state"""
        step_name = "validate_database_consistency"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            async with get_db_connection() as conn:
                # Check 1: All evaluations have valid agents
                invalid_evaluations = await conn.fetchval("""
                    SELECT COUNT(*) FROM evaluations e
                    LEFT JOIN miner_agents ma ON e.version_id = ma.version_id
                    WHERE ma.version_id IS NULL
                """)
                
                # Check 2: All agents have consistent version numbers
                version_inconsistencies = await conn.fetchval("""
                    SELECT COUNT(*) FROM miner_agents ma1
                    WHERE EXISTS (
                        SELECT 1 FROM miner_agents ma2
                        WHERE ma2.miner_hotkey = ma1.miner_hotkey
                        AND ma2.version_num = ma1.version_num
                        AND ma2.version_id != ma1.version_id
                    )
                """)
                
                # Check 3: No running evaluations exist (after reset)
                running_evaluations = await conn.fetchval("""
                    SELECT COUNT(*) FROM evaluations
                    WHERE status = 'running'
                """)
                
                issues = []
                if invalid_evaluations > 0:
                    issues.append(f"{invalid_evaluations} evaluations with invalid agents")
                if version_inconsistencies > 0:
                    issues.append(f"{version_inconsistencies} version inconsistencies")
                if running_evaluations > 0:
                    issues.append(f"{running_evaluations} still running evaluations")
                
                if issues:
                    return {
                        "step": step_name,
                        "success": False,
                        "message": f"Database consistency issues found: {', '.join(issues)}",
                        "issues": issues
                    }
                else:
                    return {
                        "step": step_name,
                        "success": True,
                        "message": "Database is consistent"
                    }
                
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def _start_health_monitoring(self) -> Dict:
        """Start the health monitoring system"""
        step_name = "start_health_monitoring"
        logger.info(f"Recovery step: {step_name}")
        
        try:
            # Start health monitor in background
            asyncio.create_task(health_monitor.start(check_interval=60))
            
            # Wait a moment to ensure it starts
            await asyncio.sleep(1)
            
            if health_monitor.is_running:
                return {
                    "step": step_name,
                    "success": True,
                    "message": "Health monitoring started successfully"
                }
            else:
                return {
                    "step": step_name,
                    "success": False,
                    "message": "Health monitoring failed to start"
                }
                
        except Exception as e:
            logger.error(f"Error in {step_name}: {e}")
            return {
                "step": step_name,
                "success": False,
                "error": str(e)
            }
    
    async def get_recovery_status(self) -> Dict:
        """Get the status of the recovery process"""
        return {
            "completed": self.recovery_completed,
            "results": self.recovery_results
        }


# Global recovery manager instance
recovery_manager = StartupRecoveryManager()