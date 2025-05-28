import sqlite3
from pathlib import Path
from typing import List

SCHEMA_VERSION = 1

def get_schema_v1() -> List[str]:
    """Database schema for version 1"""
    return [
        # Codegen challenges table
        """
        CREATE TABLE IF NOT EXISTS codegen_challenges (
            challenge_id TEXT PRIMARY KEY,  -- UUID for the challenge
            created_at TIMESTAMP NOT NULL,
            problem_statement TEXT NOT NULL,
            dynamic_checklist TEXT NOT NULL,  -- Stored as JSON array
            repository_name TEXT NOT NULL,
            commit_hash TEXT,
            context_file_paths TEXT NOT NULL -- JSON array of file paths relative to repo root
        )
        """,

        # Challenge assignments table
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
            FOREIGN KEY (challenge_id) REFERENCES codegen_challenges(challenge_id),
            UNIQUE(challenge_id, miner_hotkey)
        )
        """,

        # Responses table
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
            response_patch TEXT,
            FOREIGN KEY (challenge_id) REFERENCES codegen_challenges(challenge_id),
            FOREIGN KEY (challenge_id, miner_hotkey) REFERENCES challenge_assignments(challenge_id, miner_hotkey)
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
        
        # Required tables
        required_tables = {
            'codegen_challenges',
            'challenge_assignments',
            'responses',
            'availability_checks',
        }
        
        # Check if all required tables exist
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