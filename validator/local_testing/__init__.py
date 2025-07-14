"""
Local testing module for running agent evaluations locally without database infrastructure.

This module provides:
- LocalSandboxManager: Manages Docker containers and sandboxes for local testing
- Local evaluation runner: Orchestrates running multiple evaluations
- Setup utilities: Handles Docker image pulling and environment setup
"""

from .local_manager import LocalSandboxManager
from .runner import run_local_evaluations
from .setup import setup_local_testing_environment

__all__ = [
    "LocalSandboxManager",
    "run_local_evaluations", 
    "setup_local_testing_environment"
] 