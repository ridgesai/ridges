"""
Database wrapper utility for handling connection pool exhaustion gracefully.
"""

import functools
import asyncio
from typing import Any, Callable, Optional
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError, DisconnectionError
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

def handle_db_connection_errors(retry_count: int = 2, delay: float = 1.0):
    """
    Decorator to handle database connection errors and pool exhaustion.
    
    Args:
        retry_count: Number of retries after initial failure
        delay: Delay between retries in seconds
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            for attempt in range(retry_count + 1):
                try:
                    return await func(*args, **kwargs)
                except SQLAlchemyTimeoutError as e:
                    if "QueuePool limit" in str(e) or "connection timed out" in str(e):
                        logger.error(f"Connection pool exhausted in {func.__name__}, attempt {attempt + 1}/{retry_count + 1}: {e}")
                        if attempt < retry_count:
                            await asyncio.sleep(delay * (attempt + 1))  # Exponential backoff
                            continue
                        else:
                            logger.error(f"Connection pool exhausted in {func.__name__}, all retries failed")
                            raise
                    else:
                        logger.error(f"SQL timeout error in {func.__name__}: {e}")
                        raise
                except DisconnectionError as e:
                    logger.error(f"Database disconnection error in {func.__name__}: {e}")
                    if attempt < retry_count:
                        await asyncio.sleep(delay * (attempt + 1))
                        continue
                    else:
                        raise
                except Exception as e:
                    # Don't retry on other exceptions
                    logger.error(f"Unexpected error in {func.__name__}: {e}")
                    raise
            
            return None  # Should never reach here
        
        return wrapper
    return decorator

async def check_pool_health(db_manager) -> bool:
    """
    Check if the connection pool is healthy.
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        bool: True if pool is healthy, False if overloaded
    """
    try:
        pool_status = db_manager.get_pool_status()
        if "error" in pool_status:
            return False
        
        # Consider pool unhealthy if >90% of connections are in use
        usage_ratio = pool_status["checked_out"] / pool_status["pool_size"]
        if usage_ratio > 0.9:
            logger.warning(f"Connection pool health check failed: {usage_ratio:.2%} usage")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error checking pool health: {e}")
        return False 