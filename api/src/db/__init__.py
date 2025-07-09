"""
Database module for the Ridges API.

This module provides database operations with both traditional psycopg2 
and modern SQLAlchemy approaches.
"""

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
    'Base',
    'Agent',
    'AgentVersion',
    'Evaluation',
    'EvaluationRun',
    'WeightsHistory',
    'BannedHotkey'
]