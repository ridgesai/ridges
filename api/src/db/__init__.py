"""
Database module for the Ridges API.

This module provides database operations with both traditional psycopg2 
and modern SQLAlchemy approaches.
"""

from .operations import DatabaseManager
from .sqlalchemy_models import (
    Base,
    AgentModel,
    AgentVersionModel,
    EvaluationModel,
    EvaluationRunModel,
    WeightsHistoryModel
)
from .sqlalchemy_manager import SQLAlchemyDatabaseManager

__all__ = [
    'DatabaseManager',
    'SQLAlchemyDatabaseManager',
    'Base',
    'AgentModel',
    'AgentVersionModel',
    'EvaluationModel',
    'EvaluationRunModel',
    'WeightsHistoryModel'
]