import asyncio
from datetime import datetime, timezone
from typing import Dict, Set
from loggers.logging_utils import get_logger
from api.src.backend.db_manager import get_transaction

logger = get_logger(__name__)

class ThresholdScheduler:
    """Manages asyncio scheduling for future agent approvals based on threshold decay"""
    
    def __init__(self):
        self._scheduled_tasks: Dict[str, asyncio.Task] = {}
        self._pending_approvals: Set[str] = set()
    
    def schedule_future_approval(self, version_id: str, set_id: int, approval_time: datetime) -> None:
        """Schedule an agent for future approval when threshold decays to their score"""
        task_key = f"{version_id}_{set_id}"
        
        # Cancel existing task if present
        if task_key in self._scheduled_tasks:
            self._scheduled_tasks[task_key].cancel()
        
        # Calculate delay
        now = datetime.now(timezone.utc)
        delay_seconds = (approval_time - now).total_seconds()
        
        if delay_seconds <= 0:
            # Approve immediately if time has already passed
            asyncio.create_task(self._approve_agent_now(version_id, set_id))
            return
        
        # Schedule future approval
        task = asyncio.create_task(self._approve_agent_delayed(version_id, set_id, delay_seconds))
        self._scheduled_tasks[task_key] = task
        self._pending_approvals.add(task_key)
        
        logger.info(f"Scheduled approval for {version_id} on set {set_id} in {delay_seconds:.0f} seconds")
    
    async def _approve_agent_delayed(self, version_id: str, set_id: int, delay_seconds: float) -> None:
        """Wait for delay then approve the agent"""
        task_key = f"{version_id}_{set_id}"
        
        try:
            await asyncio.sleep(delay_seconds)
            await self._approve_agent_now(version_id, set_id)
            logger.info(f"Successfully approved {version_id} for set {set_id} after threshold decay")
        except asyncio.CancelledError:
            logger.info(f"Cancelled scheduled approval for {version_id} on set {set_id}")
        except Exception as e:
            logger.error(f"Error in scheduled approval for {version_id} on set {set_id}: {e}")
        finally:
            # Clean up
            self._scheduled_tasks.pop(task_key, None)
            self._pending_approvals.discard(task_key)
    
    async def _approve_agent_now(self, version_id: str, set_id: int) -> None:
        """Approve agent and insert into top_approved_agents_history"""
        from api.src.backend.queries.agents import approve_agent_version
        
        try:
            # Approve the agent with current timestamp
            await approve_agent_version(str(version_id), set_id, None)
            
            # Insert into top_approved_agents_history
            async with get_transaction() as conn:
                await conn.execute("""
                    INSERT INTO approved_top_agents_history (version_id, set_id, top_at)
                    VALUES ($1, $2, NOW())
                """, version_id, set_id)
            
            logger.info(f"Agent {version_id} approved and added to top agents history for set {set_id}")
            
        except Exception as e:
            logger.error(f"Failed to approve agent {version_id} for set {set_id}: {e}")
    
    def cancel_scheduled_approval(self, version_id: str, set_id: int) -> bool:
        """Cancel a scheduled approval if it exists"""
        task_key = f"{version_id}_{set_id}"
        
        if task_key in self._scheduled_tasks:
            self._scheduled_tasks[task_key].cancel()
            self._scheduled_tasks.pop(task_key)
            self._pending_approvals.discard(task_key)
            logger.info(f"Cancelled scheduled approval for {version_id} on set {set_id}")
            return True
        
        return False
    
    async def recover_pending_approvals(self) -> None:
        """Recover pending approvals on server startup"""
        logger.info("Recovering pending threshold-based approvals...")
        
        try:
            async with get_transaction() as conn:
                # Find agents that should be approved but aren't yet
                # This is a simplified recovery - in production you might want to store scheduled approvals in DB
                from api.src.backend.queries.scores import evaluate_agent_for_threshold_approval
                
                # Get the latest set_id
                max_set_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
                if not max_set_result or not max_set_result['max_set_id']:
                    return
                
                max_set_id = max_set_result['max_set_id']
                
                # Get all non-approved agents with scores in the latest set
                unapproved_agents = await conn.fetch("""
                    SELECT version_id FROM agent_scores 
                    WHERE set_id = $1 AND approved = false
                """, max_set_id)
                
                recovery_count = 0
                for row in unapproved_agents:
                    version_id = row['version_id']
                    
                    # Evaluate each agent for threshold approval
                    result = await evaluate_agent_for_threshold_approval(conn, version_id, max_set_id)
                    
                    if result['action'] == 'approve_now':
                        await self._approve_agent_now(version_id, max_set_id)
                        recovery_count += 1
                    elif result['action'] == 'approve_future':
                        self.schedule_future_approval(version_id, max_set_id, result['future_approval_time'])
                        recovery_count += 1
                
                logger.info(f"Recovered {recovery_count} pending approvals")
                
        except Exception as e:
            logger.error(f"Error recovering pending approvals: {e}")
    
    def get_scheduled_count(self) -> int:
        """Get count of currently scheduled approvals"""
        return len(self._scheduled_tasks)
    
    def get_pending_approvals(self) -> Set[str]:
        """Get set of pending approval task keys"""
        return self._pending_approvals.copy()

# Global scheduler instance
threshold_scheduler = ThresholdScheduler()
