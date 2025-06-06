import sqlite3
from pathlib import Path
from typing import List

SCHEMA_VERSION = 1

def get_schema_v1() -> List[str]:
    """Database schema for version 1 - Unified challenge structure using class table inheritance"""
    return [
        # Parent challenges table with common fields
        """
        CREATE TABLE IF NOT EXISTS challenges (
            challenge_id TEXT PRIMARY KEY,  -- UUID for the challenge
            type TEXT NOT NULL CHECK(type IN ('codegen', 'regression')),
            validator_hotkey TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
        """,

        # Codegen-specific challenges table (inherits from challenges)
        """
        CREATE TABLE IF NOT EXISTS codegen_challenges (
            challenge_id TEXT PRIMARY KEY,
            problem_statement TEXT NOT NULL, -- Problem statement for codegen challenges
            dynamic_checklist TEXT NOT NULL,  -- Stored as JSON array
            repository_url TEXT NOT NULL,     -- URL of the repository
            commit_hash TEXT,                 -- Optional commit hash for codegen challenges
            context_file_paths TEXT NOT NULL, -- JSON array of file paths relative to repo root
            FOREIGN KEY (challenge_id) REFERENCES challenges(challenge_id) ON DELETE CASCADE
        )
        """,

        # Regression-specific challenges table (inherits from challenges)  
        """
        CREATE TABLE IF NOT EXISTS regression_challenges (
            challenge_id TEXT PRIMARY KEY,
            problem_statement TEXT NOT NULL, -- Problem statement for regression challenges
            repository_url TEXT NOT NULL,
            commit_hash TEXT,                 -- Optional commit hash for regression challenges
            context_file_paths TEXT NOT NULL, -- JSON array of file paths relative to repo root
            FOREIGN KEY (challenge_id) REFERENCES challenges(challenge_id) ON DELETE CASCADE
        )
        """,

        # Unified challenge assignments table
        """
        CREATE TABLE IF NOT EXISTS challenge_assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id TEXT NOT NULL,  -- UUID for the problem
            miner_hotkey TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT CHECK(status IN ('assigned', 'sent', 'completed', 'failed')) DEFAULT 'assigned',
            FOREIGN KEY (challenge_id) REFERENCES challenges(challenge_id),
            UNIQUE(challenge_id, miner_hotkey)
        )
        """,

        # Parent responses table with common fields
        """
        CREATE TABLE IF NOT EXISTS responses (
            response_id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id TEXT NOT NULL,  -- UUID for the problem
            miner_hotkey TEXT NOT NULL,
            node_id INTEGER,
            processing_time FLOAT,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            evaluated BOOLEAN DEFAULT FALSE,
            score FLOAT,
            evaluated_at TIMESTAMP,
            FOREIGN KEY (challenge_id) REFERENCES challenges(challenge_id),
            FOREIGN KEY (challenge_id, miner_hotkey) REFERENCES challenge_assignments(challenge_id, miner_hotkey)
        )
        """,

        # Codegen-specific responses table (inherits from responses)
        """
        CREATE TABLE IF NOT EXISTS codegen_responses (
            response_id INTEGER PRIMARY KEY,
            challenge_id TEXT NOT NULL,
            response_patch TEXT NOT NULL,
            FOREIGN KEY (response_id) REFERENCES responses(response_id) ON DELETE CASCADE,
            FOREIGN KEY (challenge_id) REFERENCES codegen_challenges(challenge_id) ON DELETE CASCADE
        )
        """,

        # Regression-specific responses table (inherits from responses)
        """
        CREATE TABLE IF NOT EXISTS regression_responses (
            response_id INTEGER PRIMARY KEY,
            challenge_id TEXT NOT NULL,
            response_patch TEXT NOT NULL,
            FOREIGN KEY (response_id) REFERENCES responses(response_id) ON DELETE CASCADE,
            FOREIGN KEY (challenge_id) REFERENCES regression_challenges(challenge_id) ON DELETE CASCADE
        )
        """,

        # Availability checks table
        """
        CREATE TABLE IF NOT EXISTS availability_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            hotkey TEXT NOT NULL,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_available BOOLEAN NOT NULL,
            response_time_ms FLOAT NOT NULL,
            error TEXT
        )
        """,
    ]

def check_db_initialized(db_path: str) -> bool:
    """Check if database exists and has all required tables."""
    if not Path(db_path).exists():
        return False
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get list of all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        
        # Required tables for unified schema
        required_tables = {
            'challenges',
            'codegen_challenges',
            'regression_challenges',
            'challenge_assignments',
            'responses',
            'codegen_responses',
            'regression_responses',
            'availability_checks',
        }
        
        return required_tables.issubset(existing_tables)
        
    except sqlite3.Error:
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the database with all tables if it doesn't exist."""
    # Create directory if it doesn't exist
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    
    # Create and initialize database if needed
    if not check_db_initialized(db_path):
        conn = sqlite3.connect(db_path)
        for query in get_schema_v1():
            conn.execute(query)
        conn.commit()
        return conn
    
    # If database exists and is initialized, just return connection
    return sqlite3.connect(db_path)