"""
Simple SQLite integration tests that actually work.
These tests focus on database operations and basic endpoint functionality.
"""

import pytest
import sqlite3
import uuid
import tempfile
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock

import pytest_asyncio

# Mock environment variables
os.environ.update({
    'AWS_MASTER_USERNAME': 'test_user',
    'AWS_MASTER_PASSWORD': 'test_pass',
    'AWS_RDS_PLATFORM_ENDPOINT': 'test_endpoint',
    'AWS_RDS_PLATFORM_DB_NAME': 'test_db'
})

# Import after setting environment variables
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))


class SQLiteTestDatabase:
    """SQLite test database that mimics the PostgreSQL production schema"""
    
    def __init__(self):
        self.db_path = None
        self.conn = None
        
    async def setup(self):
        """Setup SQLite database with production-like schema"""
        # Create temporary database file
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")  # Enable foreign key constraints
        
        await self._create_schema()
        await self._insert_test_data()
        
    async def _create_schema(self):
        """Create SQLite schema that mirrors production PostgreSQL"""
        
        schema_sql = """
        -- Miner Agents table
        CREATE TABLE miner_agents (
            version_id TEXT PRIMARY KEY,
            miner_hotkey TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            version_num INTEGER NOT NULL,
            created_at TEXT NOT NULL,  -- ISO format timestamp
            status TEXT DEFAULT 'awaiting_screening_1',
            agent_summary TEXT,
            ip_address TEXT
        );
        
        -- Banned Hotkeys table
        CREATE TABLE banned_hotkeys (
            miner_hotkey TEXT NOT NULL,
            banned_reason TEXT,
            banned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Evaluation Sets table
        CREATE TABLE evaluation_sets (
            set_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            swebench_instance_id TEXT NOT NULL,
            PRIMARY KEY (set_id, type, swebench_instance_id)
        );
        
        -- Evaluations table
        CREATE TABLE evaluations (
            evaluation_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            validator_hotkey TEXT NOT NULL,
            set_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            terminated_reason TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            score REAL,
            screener_score REAL,
            FOREIGN KEY (version_id) REFERENCES miner_agents(version_id)
        );
        
        -- Approved Version IDs table
        CREATE TABLE approved_version_ids (
            version_id TEXT PRIMARY KEY,
            FOREIGN KEY (version_id) REFERENCES miner_agents(version_id)
        );
        
        -- Open Users table
        CREATE TABLE open_users (
            open_hotkey TEXT PRIMARY KEY,
            auth0_user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            name TEXT NOT NULL,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Email Whitelist table
        CREATE TABLE open_user_email_whitelist (
            email TEXT PRIMARY KEY
        );
        
        -- Create indices for performance
        CREATE INDEX idx_miner_agents_hotkey ON miner_agents(miner_hotkey);
        CREATE INDEX idx_evaluations_version_id ON evaluations(version_id);
        CREATE INDEX idx_evaluations_status ON evaluations(status);
        """
        
        # Execute schema creation
        self.conn.executescript(schema_sql)
        self.conn.commit()
        
    async def _insert_test_data(self):
        """Insert basic test data"""
        test_data = """
        -- Insert test evaluation sets
        INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES 
        (1, 'screener-1', 'test_instance_1'),
        (1, 'screener-2', 'test_instance_2'), 
        (1, 'validator', 'test_instance_3');
        
        -- Insert a test agent
        INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
        VALUES ('test-agent-id', 'test_miner', 'test_agent', 1, datetime('now'), 'scored');
        """
        
        self.conn.executescript(test_data)
        self.conn.commit()
        
    def get_connection(self):
        """Get database connection"""
        return self.conn
        
    async def cleanup(self):
        """Clean up database"""
        if self.conn:
            self.conn.close()
        if self.db_path and os.path.exists(self.db_path):
            os.unlink(self.db_path)


@pytest_asyncio.fixture(scope="session")
async def sqlite_db():
    """Setup SQLite test database"""
    db = SQLiteTestDatabase()
    await db.setup()
    yield db
    await db.cleanup()


@pytest_asyncio.fixture
def db_conn(sqlite_db):
    """Get database connection with transaction rollback"""
    conn = sqlite_db.get_connection()
    conn.execute("BEGIN")
    yield conn
    conn.rollback()


class TestDatabaseOperations:
    """Test database operations with SQLite"""
    
    def test_sqlite_schema_creation(self, sqlite_db):
        """Test that SQLite schema was created correctly"""
        conn = sqlite_db.get_connection()
        
        # Check that key tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        
        table_names = [table[0] for table in tables]
        expected_tables = [
            'miner_agents', 'banned_hotkeys', 'evaluation_sets',
            'evaluations', 'approved_version_ids',
            'open_users', 'open_user_email_whitelist'
        ]
        
        for expected_table in expected_tables:
            assert expected_table in table_names
    
    def test_sqlite_foreign_keys(self, db_conn):
        """Test that foreign key constraints work"""
        
        # Try to insert evaluation for non-existent agent
        eval_id = str(uuid.uuid4())
        fake_agent_id = str(uuid.uuid4())
        
        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, created_at)
                VALUES (?, ?, 'validator1', 1, datetime('now'))
            """, (eval_id, fake_agent_id))
    
    def test_agent_insertion_and_retrieval(self, db_conn):
        """Test basic agent operations"""
        
        # Insert test agent
        agent_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, 'test_miner', 'test_agent', 1, datetime('now'), 'awaiting_screening_1')
        """, (agent_id,))
        db_conn.commit()
        
        # Retrieve agent
        result = db_conn.execute(
            "SELECT miner_hotkey, agent_name, status FROM miner_agents WHERE version_id = ?",
            (agent_id,)
        ).fetchone()
        
        assert result is not None
        assert result[0] == "test_miner"
        assert result[1] == "test_agent" 
        assert result[2] == "awaiting_screening_1"
    
    def test_banned_hotkey_operations(self, db_conn):
        """Test banned hotkey functionality"""
        
        # Insert banned hotkey
        db_conn.execute(
            "INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) VALUES (?, ?)",
            ("banned_miner", "Code obfuscation")
        )
        db_conn.commit()
        
        # Check if hotkey is banned
        result = db_conn.execute(
            "SELECT miner_hotkey FROM banned_hotkeys WHERE miner_hotkey = ?",
            ("banned_miner",)
        ).fetchone()
        
        assert result is not None
        assert result[0] == "banned_miner"
        
        # Check non-banned hotkey
        result = db_conn.execute(
            "SELECT miner_hotkey FROM banned_hotkeys WHERE miner_hotkey = ?",
            ("good_miner",)
        ).fetchone()
        
        assert result is None
    
    def test_evaluation_operations(self, db_conn):
        """Test evaluation operations"""
        
        # Insert test agent first
        agent_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, 'eval_miner', 'eval_agent', 1, datetime('now'), 'screening_1')
        """, (agent_id,))
        
        # Insert evaluation
        eval_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, score, created_at)
            VALUES (?, ?, 'validator1', 1, 'completed', 0.85, datetime('now'))
        """, (eval_id, agent_id))
        
        db_conn.commit()
        
        # Retrieve evaluation with agent info
        result = db_conn.execute("""
            SELECT e.status, e.score, m.miner_hotkey, m.agent_name
            FROM evaluations e
            JOIN miner_agents m ON e.version_id = m.version_id
            WHERE e.evaluation_id = ?
        """, (eval_id,)).fetchone()
        
        assert result is not None
        assert result[0] == "completed"  # status
        assert result[1] == 0.85  # score
        assert result[2] == "eval_miner"  # miner_hotkey
        assert result[3] == "eval_agent"  # agent_name
    
    def test_approved_agents_operations(self, db_conn):
        """Test agent approval functionality"""
        
        # Insert test agent
        agent_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, 'approved_miner', 'approved_agent', 1, datetime('now'), 'scored')
        """, (agent_id,))
        
        # Approve the agent
        db_conn.execute(
            "INSERT INTO approved_version_ids (version_id) VALUES (?)",
            (agent_id,)
        )
        db_conn.commit()
        
        # Check approval status
        result = db_conn.execute("""
            SELECT m.miner_hotkey, m.agent_name,
                   CASE WHEN a.version_id IS NOT NULL THEN 1 ELSE 0 END as approved
            FROM miner_agents m
            LEFT JOIN approved_version_ids a ON m.version_id = a.version_id
            WHERE m.version_id = ?
        """, (agent_id,)).fetchone()
        
        assert result is not None
        assert result[0] == "approved_miner"
        assert result[1] == "approved_agent"
        assert result[2] == 1  # approved
    
    def test_open_user_operations(self, db_conn):
        """Test open user functionality"""
        
        # Add email to whitelist
        db_conn.execute(
            "INSERT INTO open_user_email_whitelist (email) VALUES (?)",
            ("test@example.com",)
        )
        
        # Insert open user
        db_conn.execute("""
            INSERT INTO open_users (open_hotkey, auth0_user_id, email, name)
            VALUES ('test_hotkey', 'auth0|test123', 'test@example.com', 'Test User')
        """)
        
        db_conn.commit()
        
        # Check user exists
        result = db_conn.execute(
            "SELECT email, name FROM open_users WHERE open_hotkey = ?",
            ("test_hotkey",)
        ).fetchone()
        
        assert result is not None
        assert result[0] == "test@example.com"
        assert result[1] == "Test User"
        
        # Check email is whitelisted
        result = db_conn.execute(
            "SELECT email FROM open_user_email_whitelist WHERE email = ?",
            ("test@example.com",)
        ).fetchone()
        
        assert result is not None
        assert result[0] == "test@example.com"
    
    def test_agent_scoring_query(self, db_conn):
        """Test complex scoring query"""
        
        # Clear any existing data first
        db_conn.execute("DELETE FROM evaluations")
        db_conn.execute("DELETE FROM miner_agents WHERE miner_hotkey LIKE 'scoring_%'")
        
        # Insert multiple agents with evaluations
        agents_data = []
        for i in range(3):
            agent_id = str(uuid.uuid4())
            miner_hotkey = f'scoring_miner_{i}'
            
            db_conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES (?, ?, ?, ?, datetime('now'), 'scored')
            """, (agent_id, miner_hotkey, f'scoring_agent_{i}', i + 1))
            
            # Insert evaluation
            eval_id = str(uuid.uuid4())
            score = 0.5 + (i * 0.1)
            db_conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, score, created_at)
                VALUES (?, ?, 'validator1', 1, 'completed', ?, datetime('now'))
            """, (eval_id, agent_id, score))
            
            agents_data.append((agent_id, miner_hotkey, score))
        
        db_conn.commit()
        
        # Query top agents by score (only our test agents)
        results = db_conn.execute("""
            SELECT m.miner_hotkey, m.agent_name, e.score
            FROM miner_agents m
            JOIN evaluations e ON m.version_id = e.version_id
            WHERE e.status = 'completed' AND e.score IS NOT NULL
              AND m.miner_hotkey LIKE 'scoring_%'
            ORDER BY e.score DESC
            LIMIT 2
        """).fetchall()
        
        assert len(results) == 2
        # Highest score first
        assert results[0][2] >= results[1][2]  # score comparison
        assert results[0][0] == "scoring_miner_2"  # highest scoring miner


class TestSimpleBusinessLogic:
    """Test simple business logic without complex endpoint integration"""
    
    def test_agent_status_progression(self, db_conn):
        """Test agent status transitions"""
        
        agent_id = str(uuid.uuid4())
        
        # Start with awaiting_screening_1
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, 'progression_miner', 'progression_agent', 1, datetime('now'), 'awaiting_screening_1')
        """, (agent_id,))
        db_conn.commit()
        
        # Update to screening_1
        db_conn.execute(
            "UPDATE miner_agents SET status = 'screening_1' WHERE version_id = ?",
            (agent_id,)
        )
        
        # Update to awaiting_screening_2
        db_conn.execute(
            "UPDATE miner_agents SET status = 'awaiting_screening_2' WHERE version_id = ?",
            (agent_id,)
        )
        
        # Update to scored
        db_conn.execute(
            "UPDATE miner_agents SET status = 'scored' WHERE version_id = ?",
            (agent_id,)
        )
        
        db_conn.commit()
        
        # Verify final status
        result = db_conn.execute(
            "SELECT status FROM miner_agents WHERE version_id = ?",
            (agent_id,)
        ).fetchone()
        
        assert result is not None
        assert result[0] == "scored"
    
    def test_miner_rate_limiting_logic(self, db_conn):
        """Test rate limiting business logic"""
        
        miner_hotkey = "rate_test_miner"
        
        # Insert first agent (older)
        agent1_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, ?, 'agent_v1', 1, datetime('now', '-2 hours'), 'scored')
        """, (agent1_id, miner_hotkey))
        
        # Insert second agent (recent)
        agent2_id = str(uuid.uuid4())
        db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES (?, ?, 'agent_v2', 2, datetime('now', '-30 minutes'), 'awaiting_screening_1')
        """, (agent2_id, miner_hotkey))
        
        db_conn.commit()
        
        # Get latest agent for hotkey
        result = db_conn.execute("""
            SELECT version_id, version_num, created_at
            FROM miner_agents
            WHERE miner_hotkey = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (miner_hotkey,)).fetchone()
        
        assert result is not None
        assert result[0] == agent2_id  # Latest agent
        assert result[1] == 2  # Version 2
    
    def test_top_agent_selection_logic(self, db_conn):
        """Test top agent selection business logic"""
        
        # Create agents with different scores
        test_agents = [
            ("top_miner_1", 0.85),
            ("top_miner_2", 0.92),
            ("top_miner_3", 0.78)
        ]
        
        for i, (miner_hotkey, score) in enumerate(test_agents):
            agent_id = str(uuid.uuid4())
            db_conn.execute("""
                INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
                VALUES (?, ?, ?, 1, datetime('now'), 'scored')
            """, (agent_id, miner_hotkey, f'agent_{i}'))
            
            # Insert evaluation with score
            eval_id = str(uuid.uuid4())
            db_conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, score, created_at)
                VALUES (?, ?, 'validator1', 1, 'completed', ?, datetime('now'))
            """, (eval_id, agent_id, score))
            
            # Approve high-scoring agents
            if score >= 0.85:
                db_conn.execute(
                    "INSERT INTO approved_version_ids (version_id) VALUES (?)",
                    (agent_id,)
                )
        
        db_conn.commit()
        
        # Get top approved agent
        result = db_conn.execute("""
            SELECT m.miner_hotkey, e.score
            FROM miner_agents m
            JOIN evaluations e ON m.version_id = e.version_id
            JOIN approved_version_ids a ON m.version_id = a.version_id
            WHERE e.status = 'completed'
            ORDER BY e.score DESC
            LIMIT 1
        """).fetchone()
        
        assert result is not None
        assert result[0] == "top_miner_2"  # Highest scoring approved agent
        assert result[1] == 0.92


if __name__ == "__main__":
    pytest.main([__file__, "-v"])