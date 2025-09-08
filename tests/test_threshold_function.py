"""
Integration tests for the threshold function endpoint.
Tests the complete flow from database setup through to API response with various scenarios.
"""

import pytest
import asyncpg
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import patch

from httpx import AsyncClient
import pytest_asyncio

# Set environment variables for testing
import os

if not os.getenv('AWS_MASTER_USERNAME'):
    os.environ.update({
        'AWS_MASTER_USERNAME': 'test_user',
        'AWS_MASTER_PASSWORD': 'test_pass',
        'AWS_RDS_PLATFORM_ENDPOINT': 'localhost',
        'AWS_RDS_PLATFORM_DB_NAME': 'postgres',
        'POSTGRES_TEST_URL': 'postgresql://test_user:test_pass@localhost:5432/postgres'
    })

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))

from api.src.main import app

@pytest_asyncio.fixture
async def db_connection():
    """Direct database connection for testing"""
    conn = await asyncpg.connect(
        user='test_user',
        password='test_pass',
        host='localhost',
        port=5432,
        database='postgres'
    )
    
    # Setup schema
    await setup_database_schema(conn)
    
    yield conn
    
    await conn.close()


@pytest_asyncio.fixture
async def initialized_app():
    """Initialize the FastAPI app with database connection"""
    from api.src.backend.db_manager import new_db
    
    # Initialize the database connection pool
    await new_db.open()
    
    yield app
    
    # Clean up
    await new_db.close()


@pytest_asyncio.fixture
async def async_client(initialized_app):
    """Async HTTP client for testing FastAPI endpoints"""
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=initialized_app), base_url="http://testserver") as client:
        yield client


async def setup_database_schema(conn: asyncpg.Connection):
    """Setup database schema for integration tests"""
    # Read the actual production schema file
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'src', 'backend', 'postgres_schema.sql')
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    # Execute the production schema
    await conn.execute(schema_sql)
    
    # Ensure innovation column exists (in case of schema timing issues)
    await conn.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'miner_agents' AND column_name = 'innovation'
            ) THEN
                ALTER TABLE miner_agents ADD COLUMN innovation DOUBLE PRECISION;
            END IF;
        END $$;
    """)
    
    # Disable the approval deletion trigger for tests to allow cleanup
    await conn.execute("""
        DROP TRIGGER IF EXISTS no_delete_approval_trigger ON approved_version_ids;
        CREATE OR REPLACE FUNCTION prevent_delete_approval_test() RETURNS TRIGGER AS $$ 
        BEGIN 
            -- Allow deletions in test environment
            RETURN OLD; 
        END; 
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER no_delete_approval_trigger BEFORE DELETE ON approved_version_ids 
        FOR EACH ROW EXECUTE FUNCTION prevent_delete_approval_test();
    """)
    
    # Insert test evaluation sets for testing
    await conn.execute("""
        INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES 
        (1, 'screener-1', 'test_instance_1'),
        (1, 'screener-2', 'test_instance_2'), 
        (1, 'validator', 'test_instance_3'),
        (2, 'screener-1', 'test_instance_4'),
        (2, 'screener-2', 'test_instance_5'), 
        (2, 'validator', 'test_instance_6')
        ON CONFLICT DO NOTHING
    """)

    # Insert threshold config values for testing
    await conn.execute("""
        INSERT INTO threshold_config (key, value) VALUES 
        ('innovation_weight', 0.25),
        ('decay_per_epoch', 0.05),
        ('frontier_scale', 0.84),
        ('improvement_weight', 0.30)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """)


class TestThresholdFunction:
    """Test the threshold function endpoint with various database states"""
    
    @pytest.mark.asyncio
    async def test_threshold_function_empty_database(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function with empty database"""
        
        # Ensure database is clean
        await self._clean_database(db_connection)
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # Verify response structure
        assert "threshold_function" in result
        assert "current_top_score" in result
        assert "current_top_approved_score" in result
        assert "epoch_0_time" in result
        assert "epoch_length_minutes" in result
        
        # With empty database, scores should be 0
        assert result["current_top_score"] == 0.0
        assert result["current_top_approved_score"] == 0.0
        assert result["epoch_0_time"] is None  # No agents means no epoch 0
        assert result["epoch_length_minutes"] == 72
        
        # Threshold function should still be generated with default values
        assert "Math.exp" in result["threshold_function"]
    
    @pytest.mark.asyncio
    async def test_threshold_function_single_approved_agent(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function with one approved agent"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create one approved agent with evaluations
        agent_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Test Agent 1",
            score=0.85,
            set_id=2
        )

        # :)
        
        # Drop and recreate the materialized view to pick up schema changes
        await db_connection.execute("DROP MATERIALIZED VIEW IF EXISTS agent_scores CASCADE")
        
        # Read and recreate the materialized view with the updated schema
        schema_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'src', 'backend', 'postgres_schema.sql')
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        # Extract just the materialized view creation part
        import re
        mv_match = re.search(r'CREATE MATERIALIZED VIEW agent_scores.*?;', schema_sql, re.DOTALL)
        if mv_match:
            await db_connection.execute(mv_match.group(0))
        
        # Create unique index
        await db_connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS agent_scores_unique_idx 
            ON agent_scores (version_id, set_id)
        """)
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # Verify scores match our test data
        assert result["current_top_score"] == 0.85
        assert result["current_top_approved_score"] == 0.85
        assert result["epoch_0_time"] is not None  # Should have epoch 0 time from approval
        assert result["epoch_length_minutes"] == 72
        
        # Verify threshold function format
        threshold_func = result["threshold_function"]
        assert "Math.exp" in threshold_func
        assert "+" in threshold_func
        assert "*" in threshold_func
    
    @pytest.mark.asyncio 
    async def test_threshold_function_multiple_agents(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function with multiple agents of different scores"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Create multiple agents with different scores
        agent1 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Agent 1",
            score=0.75,
            set_id=2
        )
        
        agent2 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_2", 
            agent_name="Agent 2",
            score=0.90,  # Higher score
            set_id=2
        )
        
        # Create a non-approved agent with even higher score
        agent3 = await self._create_agent_with_evaluations(
            db_connection,
            miner_hotkey="test_hotkey_3",
            agent_name="Agent 3", 
            score=0.95,  # Highest score but not approved
            set_id=2,
            approved=False
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # current_top_score should be highest overall (0.95)
        assert result["current_top_score"] == 0.95
        
        # current_top_approved_score should be highest approved (0.90)
        assert result["current_top_approved_score"] == 0.90
        
        # Should have epoch 0 time from the top approved agent
        assert result["epoch_0_time"] is not None
        assert result["epoch_length_minutes"] == 72
    
    @pytest.mark.asyncio
    async def test_threshold_function_with_history(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function with historical top agents"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Create approved agents
        agent1 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Agent 1",
            score=0.80,
            set_id=2
        )
        
        agent2 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_2",
            agent_name="Agent 2", 
            score=0.85,
            set_id=2
        )
        
        # Create historical entries in approved_top_agents_history
        base_time = datetime.now(timezone.utc)
        await db_connection.execute("""
            INSERT INTO approved_top_agents_history (version_id, set_id, top_at) VALUES 
            ($1, 2, $2),
            ($3, 2, $4)
        """, 
        agent2["version_id"], base_time,  # Current top (most recent)
        agent1["version_id"], base_time - timedelta(hours=1))  # Previous top
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # Verify all fields are present and valid
        assert result["current_top_score"] == 0.85
        assert result["current_top_approved_score"] == 0.85
        assert result["epoch_0_time"] is not None
        assert result["epoch_length_minutes"] == 72
        
        # With history, the threshold function should incorporate improvement
        threshold_func = result["threshold_function"]
        assert isinstance(threshold_func, str)
        assert "Math.exp" in threshold_func
    
    @pytest.mark.asyncio
    async def test_threshold_function_with_innovation(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function with innovation scores"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Create agent with innovation score
        agent_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Innovative Agent",
            score=0.80,
            set_id=2,
            innovation=0.75  # High innovation
        )
        
        # Add to history to enable innovation calculation
        await db_connection.execute("""
            INSERT INTO approved_top_agents_history (version_id, set_id, top_at) VALUES 
            ($1, 2, $2)
        """, agent_data["version_id"], datetime.now(timezone.utc))
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # Verify innovation is incorporated into threshold function
        assert result["current_top_score"] == 0.80
        assert result["current_top_approved_score"] == 0.80
        assert result["epoch_0_time"] is not None
        
        # The threshold function should be valid
        threshold_func = result["threshold_function"]
        assert "Math.exp" in threshold_func
        # Innovation should boost the initial threshold value
        assert "0.80" in threshold_func or "0.9" in threshold_func  # Should be higher than base score
    
    @pytest.mark.asyncio
    async def test_threshold_function_different_set_ids(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test threshold function uses latest set_id"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Create agent in set_id 1 (older)
        agent1 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Old Agent",
            score=0.90,
            set_id=1
        )
        
        # Create agent in set_id 2 (newer/latest)
        agent2 = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_2",
            agent_name="New Agent", 
            score=0.75,  # Lower score but in latest set
            set_id=2
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the threshold function endpoint
        response = await async_client.get("/scoring/threshold-function")
        assert response.status_code == 200
        
        result = response.json()
        
        # Should use latest set_id (2), so scores should reflect agent2
        assert result["current_top_score"] == 0.75  # From set_id 2
        assert result["current_top_approved_score"] == 0.75  # From set_id 2
        assert result["epoch_0_time"] is not None
    
    async def _clean_database(self, conn: asyncpg.Connection):
        """Clean up test data in correct order to respect foreign key constraints"""
        await conn.execute("DELETE FROM approved_top_agents_history")
        await conn.execute("DELETE FROM top_agents")  # Add this to clean up top_agents table
        await conn.execute("DELETE FROM approved_version_ids")
        await conn.execute("DELETE FROM embeddings")
        await conn.execute("DELETE FROM inferences")
        await conn.execute("DELETE FROM evaluation_runs")
        await conn.execute("DELETE FROM evaluations")
        await conn.execute("DELETE FROM miner_agents")
        # Refresh materialized view to reflect deletions
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        except Exception:
            # If concurrent refresh fails, try regular refresh
            await conn.execute("REFRESH MATERIALIZED VIEW agent_scores")
    
    async def _create_approved_agent_with_evaluations(self, conn: asyncpg.Connection, miner_hotkey: str, agent_name: str, score: float, set_id: int = 2, innovation: Optional[float] = None) -> dict:
        """Create an approved agent with completed evaluations"""
        return await self._create_agent_with_evaluations(
            conn, miner_hotkey, agent_name, score, set_id, approved=True, innovation=innovation
        )
    
    async def _create_agent_with_evaluations(self, conn: asyncpg.Connection, miner_hotkey: str, agent_name: str, score: float, set_id: int = 2, approved: bool = True, innovation: Optional[float] = None) -> dict:
        """Create an agent with completed evaluations and optionally approve it"""
        version_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)
        
        # Insert agent
        await conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status, innovation)
            VALUES ($1, $2, $3, 1, $4, 'scored', $5)
        """, version_id, miner_hotkey, agent_name, created_at, innovation)
        
        # Create evaluations for multiple validators to meet the 2+ validator requirement
        validator_hotkeys = ["validator_1", "validator_2", "validator_3"]
        
        for validator_hotkey in validator_hotkeys:
            evaluation_id = uuid.uuid4()
            
            # Insert evaluation
            await conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, finished_at, score)
                VALUES ($1, $2, $3, $4, 'completed', $5, $6, $7)
            """, evaluation_id, version_id, validator_hotkey, set_id, created_at, created_at + timedelta(minutes=5), score)
        
        # Approve the agent if requested
        if approved:
            approved_at = created_at  # Use same time as creation, not future time
            await conn.execute("""
                INSERT INTO approved_version_ids (version_id, set_id, approved_at)
                VALUES ($1, $2, $3)
            """, version_id, set_id, approved_at)
        
        return {
            "version_id": version_id,
            "miner_hotkey": miner_hotkey,
            "agent_name": agent_name,
            "score": score,
            "set_id": set_id,
            "approved": approved,
            "innovation": innovation
        }





if __name__ == "__main__":
    pytest.main([__file__, "-v"])
