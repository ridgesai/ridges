import contextvars
import logging
import uuid
from typing import Optional
from contextlib import contextmanager

# Create context variables for process tracking
process_id_var = contextvars.ContextVar('process_id', default=None)
process_type_var = contextvars.ContextVar('process_type', default=None)

class ProcessTrackingFilter(logging.Filter):
    """Logging filter that automatically adds process tracking information to log records"""
    
    def filter(self, record):
        # Add process tracking fields to the log record
        record.process_id = process_id_var.get() or 'unknown'
        record.process_type = process_type_var.get() or 'unknown'
        return True

def setup_process_logging(logger: logging.Logger) -> None:
    """Set up a logger with process tracking filter and formatter"""
    # Add the process tracking filter
    process_filter = ProcessTrackingFilter()
    logger.addFilter(process_filter)
    
    # Create a formatter that includes process tracking information
    formatter = logging.Formatter(
        '%(asctime)s - PID:%(process_id)s - Type:%(process_type)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Update existing handlers with the new formatter
    for handler in logger.handlers:
        handler.setFormatter(formatter)

@contextmanager
def process_context(process_type: str):
    """
    Context manager for managing process tracking context.
    
    Args:
        process_type: Type of process (e.g., 'validator-version', 'evaluation-creation')
    """
    # Generate a new process ID
    process_id = str(uuid.uuid4())
    
    # Set context variables
    process_id_token = process_id_var.set(process_id)
    process_type_token = process_type_var.set(process_type)
    
    try:
        yield process_id
    finally:
        # Restore previous values
        process_id_var.reset(process_id_token)
        process_type_var.reset(process_type_token)

def get_current_process_id() -> Optional[str]:
    """Get the current process ID from context"""
    return process_id_var.get()

def get_current_process_type() -> Optional[str]:
    """Get the current process type from context"""
    return process_type_var.get()

def set_process_context(process_id: str, process_type: str):
    """
    Manually set process context variables.
    Use this when you can't use the context manager.
    """
    process_id_var.set(process_id)
    process_type_var.set(process_type) 