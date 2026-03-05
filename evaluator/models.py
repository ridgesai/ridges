"""Data models for sandbox execution and evaluation.

This module defines the core data structures used by the sandbox system:
- Sandbox: Represents an isolated execution environment
- SandboxResult: Base result structure for sandbox execution
- SandboxResultWithLogs: Extended result including execution logs
- EvaluationRunException: Custom exception for evaluation failures

These models use Pydantic for validation and serialization, ensuring
data integrity throughout the evaluation pipeline.
"""

import docker

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict
from models.evaluation_run import EvaluationRunErrorCode



class Sandbox(BaseModel):
    """Represents an isolated sandbox environment for code execution.
    
    A Sandbox encapsulates all resources needed to run code in an isolated
    Docker container, including the container reference, temporary directory,
    and execution timeout.
    
    Attributes:
        name: Unique identifier for this sandbox (prefixed with DOCKER_PREFIX)
        temp_dir: Path to the temporary directory mounted at /sandbox in the container
        container: The Docker container instance running the sandbox
        timeout_seconds: Maximum execution time before forced termination
        
    Lifecycle:
        1. Created by SandboxManager.initialize_sandbox()
        2. Executed by SandboxManager.run_sandbox()
        3. Automatically cleaned up after execution (container stopped/removed, temp_dir deleted)
        
    Note:
        The container attribute is a Docker SDK object, so we need
        arbitrary_types_allowed in the model config.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True) # Because of docker.models.containers.Container

    name: str
    temp_dir: str
    container: docker.models.containers.Container
    timeout_seconds: Optional[int]

class SandboxResult(BaseModel):
    """Result of a sandbox execution.
    
    This is the standard output format that scripts running inside sandboxes
    should write to /sandbox/output.json.
    
    Attributes:
        success: Whether the script executed successfully
        output: The result data (only present if success=True)
        error: Error message string (only present if success=False)
        traceback: Full traceback string (only present if success=False)
        
    Example Success:
        {
            "success": True,
            "output": {"result": "patch applied successfully"}
        }
        
    Example Failure:
        {
            "success": False,
            "error": "FileNotFoundError: patch.txt not found",
            "traceback": "Traceback (most recent call last):..."
        }
    """
    success: bool

    # if success
    output: Any = None

    # if not success
    error: Optional[str] = None
    traceback: Optional[str] = None

class SandboxResultWithLogs(SandboxResult):
    """Extended sandbox result including execution logs.
    
    This extends SandboxResult with the full stdout/stderr logs captured
    from the container during execution. Used internally by SandboxManager
    to provide complete execution context.
    
    Attributes:
        logs: Complete stdout/stderr output from the container execution
        
    Note:
        Logs can be large for long-running scripts. Consider log rotation
        or truncation if storing results long-term.
    """
    logs: str



class EvaluationRunException(Exception):
    """Exception raised during evaluation runs that should be handled by the validator.
    
    This exception provides structured error information including an error code
    (for programmatic handling) and optional extra data (like partial logs).
    
    Attributes:
        error_code: EvaluationRunErrorCode enum value categorizing the error type
        error_message: Human-readable description of what went wrong
        extra: Optional dictionary with additional context (agent_logs, eval_logs)
        
    Usage:
        ProblemSuite implementations raise this when evaluation fails in a way
        that the validator should handle specially (e.g., agent timeout vs crash).
        
        The validator catches this and converts it to an appropriate error response.
        
    Extra Field Keys:
        - agent_logs: Partial logs captured before agent failure/timeout
        - eval_logs: Partial logs captured during evaluation
        
    Example:
        raise EvaluationRunException(
            EvaluationRunErrorCode.AGENT_TIMEOUT,
            "Agent exceeded 300 second timeout",
            extra={"agent_logs": partial_logs}
        )
    """
    def __init__(self, error_code: EvaluationRunErrorCode, error_message: str, *, extra: Optional[Dict[str, Any]] = None):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.extra = extra