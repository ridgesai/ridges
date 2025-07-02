"""Task for draining validator logs to the platform."""

import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pathlib import Path

from validator.utils.logging import get_logger, logging_db_path
from validator.config import RIDGES_API_URL, validator_hotkey, LOG_DRAIN_FREQUENCY

logger = get_logger(__name__)


async def get_recent_logs(minutes: int = 20) -> List[Dict[str, Any]]:
    """
    Query the validator's logging database for logs from the last N minutes.
    
    Args:
        minutes: Number of minutes to look back for logs
        
    Returns:
        List of log dictionaries matching the platform's expected format
    """
    try:
        # Calculate the cutoff timestamp
        cutoff_time = datetime.now() - timedelta(minutes=minutes)
        
        # Connect to the logging database
        conn = sqlite3.connect(logging_db_path)
        conn.row_factory = sqlite3.Row  # This allows us to access columns by name
        
        try:
            cursor = conn.cursor()
            
            # Query logs from the last N minutes
            cursor.execute("""
                SELECT id, timestamp, levelname, name, pathname, funcName, 
                       lineno, message, active_coroutines, eval_loop_num
                FROM logs 
                WHERE datetime(timestamp) >= datetime(?)
                ORDER BY timestamp ASC
            """, (cutoff_time.isoformat(),))
            
            # Convert rows to dictionaries
            logs = []
            for row in cursor.fetchall():
                log_dict = {
                    'id': row['id'],
                    'timestamp': row['timestamp'],
                    'levelname': row['levelname'],
                    'name': row['name'],
                    'pathname': row['pathname'],
                    'funcName': row['funcName'],
                    'lineno': row['lineno'],
                    'message': row['message'],
                    'active_coroutines': row['active_coroutines'],
                    'eval_loop_num': row['eval_loop_num']
                }
                logs.append(log_dict)
            
            logger.info(f"Retrieved {len(logs)} logs from the last {minutes} minutes")
            return logs
            
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        logger.error(f"Error querying logging database: {e}")
        return []


async def send_logs_to_platform(logs: List[Dict[str, Any]]) -> bool:
    """
    Send logs to the platform via HTTP POST.
    
    Args:
        logs: List of log dictionaries to send
        
    Returns:
        True if successful, False otherwise
    """
    if not logs:
        logger.debug("No logs to send to platform")
        return True
        
    try:
        payload = {
            "validator_hotkey": validator_hotkey.ss58_address,
            "logs": logs
        }
        
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{RIDGES_API_URL}/logs",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Successfully sent {len(logs)} logs to platform. "
                       f"Response: {result.get('logs_stored', 0)} stored, "
                       f"{result.get('logs_failed', 0)} failed")
            return True
            
    except httpx.TimeoutException:
        logger.error("Timeout sending logs to platform")
        return False
    except httpx.HTTPError as e:
        logger.error(f"HTTP error sending logs to platform: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending logs to platform: {e}")
        return False


async def log_drain_task():
    """
    Main log drain task that runs every 10 minutes.
    Queries logs from the last 20 minutes and sends them to the platform.
    """
    logger.info("Starting log drain task")
    
    # Convert timedelta to seconds for sleep
    sleep_seconds = LOG_DRAIN_FREQUENCY.total_seconds()
    
    while True:
        try:
            logger.debug("Running log drain cycle")
            
            # Get logs from the last 20 minutes
            logs = await get_recent_logs(minutes=20)
            
            if logs:
                # Send logs to platform
                success = await send_logs_to_platform(logs)
                if success:
                    logger.debug(f"Log drain cycle completed successfully - sent {len(logs)} logs")
                else:
                    logger.warning(f"Log drain cycle failed to send {len(logs)} logs")
            else:
                logger.debug("No recent logs to send")
                
        except Exception as e:
            logger.error(f"Error in log drain task: {e}")
            
        # Wait for the next cycle
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("Log drain task cancelled")
            break
            
    logger.info("Log drain task stopped")