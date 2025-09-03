"""
System Metrics Collection Utility

Provides functions to collect system metrics including CPU, RAM, disk usage, and Docker container counts.
"""

import psutil
import subprocess
import asyncio
from typing import Dict, Optional
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def get_system_metrics() -> Dict[str, Optional[float]]:
    """
    Collect system metrics including CPU, RAM, disk usage, and Docker container count.
    
    Returns:
        Dict containing:
        - cpu_percent: CPU usage percentage (0-100)
        - ram_percent: RAM usage percentage (0-100) 
        - disk_percent: Disk usage percentage (0-100)
        - containers: Number of Docker containers running
    """
    metrics = {
        "cpu_percent": None,
        "ram_percent": None,
        "disk_percent": None,
        "containers": None
    }
    
    try:
        # Get CPU usage percentage
        # Use interval=1 for more accurate measurement, but run it async to avoid blocking
        loop = asyncio.get_event_loop()
        cpu_percent = await loop.run_in_executor(None, psutil.cpu_percent, 1)
        metrics["cpu_percent"] = round(cpu_percent, 1)
        
        # Get RAM usage percentage
        memory = psutil.virtual_memory()
        metrics["ram_percent"] = round(memory.percent, 1)
        
        # Get disk usage percentage for root filesystem
        disk = psutil.disk_usage('/')
        metrics["disk_percent"] = round(disk.percent, 1)
        
    except Exception as e:
        logger.warning(f"Error collecting psutil metrics: {e}")
    
    try:
        # Get Docker container count
        # Run docker ps command to count running containers
        result = await loop.run_in_executor(
            None, 
            lambda: subprocess.run(
                ["docker", "ps", "--format", "table {{.ID}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
        )
        
        if result.returncode == 0:
            # Count lines minus header (subtract 1 for header line)
            container_lines = result.stdout.strip().split('\n')
            container_count = len(container_lines) - 1 if len(container_lines) > 1 else 0
            metrics["containers"] = container_count
        else:
            logger.warning(f"Docker command failed with return code {result.returncode}: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        logger.warning("Docker ps command timed out")
    except FileNotFoundError:
        logger.warning("Docker command not found - Docker may not be installed")
    except Exception as e:
        logger.warning(f"Error getting Docker container count: {e}")
    
    return metrics

async def get_system_metrics_fast() -> Dict[str, Optional[float]]:
    """
    Get system metrics with faster/less accurate measurements for frequent updates.
    Uses instant CPU measurement instead of 1-second interval.
    
    Returns:
        Dict containing the same metrics as get_system_metrics() but with faster collection
    """
    metrics = {
        "cpu_percent": None,
        "ram_percent": None, 
        "disk_percent": None,
        "containers": None
    }
    
    try:
        # Get instant CPU usage (less accurate but faster)
        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_percent"] = round(cpu_percent, 1)
        
        # Get RAM usage percentage
        memory = psutil.virtual_memory()
        metrics["ram_percent"] = round(memory.percent, 1)
        
        # Get disk usage percentage for root filesystem
        disk = psutil.disk_usage('/')
        metrics["disk_percent"] = round(disk.percent, 1)
        
    except Exception as e:
        logger.warning(f"Error collecting psutil metrics: {e}")
    
    try:
        # Get Docker container count with shorter timeout
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "ps", "-q"],  # Just get IDs, faster
                capture_output=True,
                text=True,
                timeout=2
            )
        )
        
        if result.returncode == 0:
            container_count = len([line for line in result.stdout.strip().split('\n') if line.strip()])
            metrics["containers"] = container_count
        else:
            logger.warning(f"Docker command failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        logger.warning("Docker ps command timed out")
    except FileNotFoundError:
        logger.warning("Docker command not found")
    except Exception as e:
        logger.warning(f"Error getting Docker container count: {e}")
    
    return metrics
