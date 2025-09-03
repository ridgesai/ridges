"""
System metrics collection utility for validators/screeners.
"""

import subprocess
import asyncio
from typing import Dict, Optional
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logger.warning("psutil not available - system metrics will return None")
    PSUTIL_AVAILABLE = False

async def get_system_metrics() -> Dict[str, Optional[float]]:
    """
    Collect system metrics from this validator/screener machine.
    
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
    
    if not PSUTIL_AVAILABLE:
        return metrics
        
    try:
        # Get CPU usage (non-blocking)
        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_percent"] = round(float(cpu_percent), 1)
        
        # Get RAM usage percentage
        memory = psutil.virtual_memory()
        metrics["ram_percent"] = round(float(memory.percent), 1)
        
        # Get disk usage percentage for root filesystem
        disk = psutil.disk_usage('/')
        metrics["disk_percent"] = round(float(disk.percent), 1)
        
        logger.debug(f"Collected psutil metrics: CPU={metrics['cpu_percent']}%, RAM={metrics['ram_percent']}%, Disk={metrics['disk_percent']}%")
        
    except Exception as e:
        logger.warning(f"Error collecting psutil metrics: {e}")
    
    try:
        # Get Docker container count
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "ps", "-q"],
                capture_output=True,
                text=True,
                timeout=3
            )
        )
        
        if result.returncode == 0:
            # Count non-empty lines
            container_count = len([line for line in result.stdout.strip().split('\n') if line.strip()])
            metrics["containers"] = container_count
            logger.debug(f"Found {container_count} Docker containers")
        else:
            logger.warning(f"Docker ps failed with return code {result.returncode}: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        logger.warning("Docker ps command timed out")
    except FileNotFoundError:
        logger.warning("Docker command not found")
    except Exception as e:
        logger.warning(f"Error getting Docker container count: {e}")
    
    return metrics
