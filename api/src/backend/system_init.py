"""
System Initialization

This module handles system initialization, startup recovery,
and ensures all components are properly configured.
"""

import asyncio
import logging
from typing import Optional

from api.src.backend.startup_recovery import recovery_manager
from api.src.backend.health_monitor import health_monitor

logger = logging.getLogger(__name__)


async def initialize_evaluation_system() -> bool:
    """
    Initialize the evaluation system with full recovery and monitoring.
    
    This should be called once when the application starts.
    Returns True if initialization succeeded, False otherwise.
    """
    logger.info("Initializing evaluation system")
    
    try:
        # Step 1: Perform startup recovery
        logger.info("Performing startup recovery")
        recovery_results = await recovery_manager.perform_startup_recovery()
        
        if not recovery_results["success"]:
            logger.error("Startup recovery failed")
            return False
        
        # Step 2: Start health monitoring
        logger.info("Starting health monitoring")
        # Health monitor is started as part of recovery, but we'll verify it
        if not health_monitor.is_running:
            logger.warning("Health monitor not running, starting it")
            asyncio.create_task(health_monitor.start(check_interval=60))
            await asyncio.sleep(1)  # Give it time to start
        
        if not health_monitor.is_running:
            logger.error("Failed to start health monitor")
            return False
        
        logger.info("Evaluation system initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error initializing evaluation system: {e}")
        return False


async def shutdown_evaluation_system():
    """
    Gracefully shutdown the evaluation system.
    
    This should be called when the application is shutting down.
    """
    logger.info("Shutting down evaluation system")
    
    try:
        # Stop health monitoring
        if health_monitor.is_running:
            logger.info("Stopping health monitor")
            await health_monitor.stop()
        
        logger.info("Evaluation system shutdown complete")
        
    except Exception as e:
        logger.error(f"Error during system shutdown: {e}")


# Export main functions
__all__ = [
    "initialize_evaluation_system",
    "shutdown_evaluation_system"
]