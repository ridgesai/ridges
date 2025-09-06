from enum import IntEnum
from collections import defaultdict
from typing import Dict, Tuple, Optional
import threading
from fastapi import Request


class BadRequestErrorCode(IntEnum):
    """Enum for different 400 Bad Request error types"""
    
    # Embedding endpoint errors
    EMBEDDING_MISSING_RUN_ID = 1001
    EMBEDDING_INVALID_UUID = 1002
    EMBEDDING_WRONG_STATUS = 1003
    
    # Inference endpoint errors
    INFERENCE_MISSING_RUN_ID = 2001
    INFERENCE_INVALID_UUID = 2002
    INFERENCE_WRONG_STATUS = 2003
    
    # External API errors
    CHUTES_INFERENCE_BAD_REQUEST = 3001
    CHUTES_EMBEDDING_BAD_REQUEST = 3002
    
    # Authentication errors
    INVALID_SCREENER_PASSWORD = 4001
    UNAUTHORIZED_IP_ADDRESS = 4002


# Thread-safe tracking of IP + error code combinations
_error_tracking_lock = threading.Lock()
_error_tracking_map: Dict[Tuple[str, int], int] = defaultdict(int)


def track_400_error(client_ip: str, error_code: BadRequestErrorCode) -> None:
    """
    Track a 400 error occurrence for a specific IP and error code.
    
    Args:
        client_ip: The client's IP address
        error_code: The specific error code from BadRequestErrorCode enum
    """
    with _error_tracking_lock:
        key = (client_ip, error_code.value)
        _error_tracking_map[key] += 1


def get_error_stats(client_ip: Optional[str] = None, 
                   error_code: Optional[BadRequestErrorCode] = None) -> Dict[Tuple[str, int], int]:
    """
    Get error statistics, optionally filtered by IP or error code.
    
    Args:
        client_ip: Optional IP address to filter by
        error_code: Optional error code to filter by
        
    Returns:
        Dictionary mapping (ip, error_code) -> count
    """
    with _error_tracking_lock:
        if client_ip is None and error_code is None:
            return dict(_error_tracking_map)
        
        filtered_stats = {}
        for (ip, code), count in _error_tracking_map.items():
            if client_ip is not None and ip != client_ip:
                continue
            if error_code is not None and code != error_code.value:
                continue
            filtered_stats[(ip, code)] = count
        
        return filtered_stats


def get_client_ip(request: Request) -> str:
    """
    Extract client IP from FastAPI request.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Client IP address as string
    """

    # NOTE: If we add Cloudflare we need to look at X-Forwarded-For or X-Real-IP or similar

    # Always use direct client IP
    if request.client:
        return request.client.host
    
    return "unknown"


def clear_error_stats() -> None:
    """Clear all error tracking statistics. Useful for testing."""
    with _error_tracking_lock:
        _error_tracking_map.clear()


def get_top_error_sources(limit: int = 10) -> list:
    """
    Get the top error sources by total count.
    
    Args:
        limit: Maximum number of results to return
        
    Returns:
        List of tuples: (ip, error_code, count) sorted by count descending
    """
    with _error_tracking_lock:
        sorted_errors = sorted(
            [(ip, code, count) for (ip, code), count in _error_tracking_map.items()],
            key=lambda x: x[2],
            reverse=True
        )
        return sorted_errors[:limit]
