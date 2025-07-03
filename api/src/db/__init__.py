"""
Database module for the Ridges API.

This module provides database operations with both traditional psycopg2 
and modern SQLAlchemy approaches.
"""

from .operations import DatabaseManager
from .sqlalchemy_models import (
    Base,
    Agent,
    AgentVersion,
    Evaluation,
    EvaluationRun,
    WeightsHistory,
    BannedHotkey
)

__all__ = [
    'DatabaseManager',
    'Base',
    'Agent',
    'AgentVersion',
    'Evaluation',
    'EvaluationRun',
    'WeightsHistory',
    'BannedHotkey'
]