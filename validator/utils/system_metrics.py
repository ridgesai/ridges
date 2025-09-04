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
        - ram_total_gb: Total RAM in GB
        - disk_percent: Disk usage percentage (0-100)
        - disk_total_gb: Total disk space in GB
        - containers: Number of Docker containers running
    """
    metrics = {
        "cpu_percent": None,
        "ram_percent": None,
        "ram_total_gb": None,
        "disk_percent": None,
        "disk_total_gb": None,
        "containers": None
    }
    
    if not PSUTIL_AVAILABLE:
        return metrics
        
    try:
        # Get CPU usage (non-blocking)
        cpu_percent = psutil.cpu_percent(interval=None)
        metrics["cpu_percent"] = round(float(cpu_percent), 1)
        
        # Get RAM usage percentage and total
        memory = psutil.virtual_memory()
        metrics["ram_percent"] = round(float(memory.percent), 1)
        metrics["ram_total_gb"] = round(float(memory.total) / (1024**3), 1)  # Convert bytes to GB
        
        # Get disk usage percentage and total for root filesystem
        disk = psutil.disk_usage('/')
        metrics["disk_percent"] = round(float(disk.percent), 1)
        metrics["disk_total_gb"] = round(float(disk.total) / (1024**3), 1)  # Convert bytes to GB
        
        logger.debug(f"Collected psutil metrics: CPU={metrics['cpu_percent']}%, RAM={metrics['ram_percent']}% ({metrics['ram_total_gb']}GB total), Disk={metrics['disk_percent']}% ({metrics['disk_total_gb']}GB total)")
        
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
