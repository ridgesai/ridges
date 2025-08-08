"""
Simple integration tests for the weights function endpoint.
Tests the complete flow from database setup through to API response.
"""

import pytest
import asyncpg
import uuid
from datetime import datetime, timezone
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
    async with AsyncClient(app=initialized_app, base_url="http://testserver") as client:
        yield client


async def setup_database_schema(conn: asyncpg.Connection):
    """Setup database schema for integration tests"""
    # Read the actual production schema file
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'src', 'backend', 'postgres_schema.sql')
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    # Execute the production schema
    await conn.execute(schema_sql)
    
    # Insert test evaluation sets for testing
    await conn.execute("""
        INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES 
        (1, 'screener-1', 'test_instance_1'),
        (1, 'screener-2', 'test_instance_2'), 
        (1, 'validator', 'test_instance_3')
        ON CONFLICT DO NOTHING
    """)


class TestWeightsSetting:
    """Test the weights function with various database states"""
    
    @pytest.mark.asyncio
    async def test_weights_empty_database(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test weights function with empty database - should return empty dict"""
        
        # Ensure database is clean
        await self._clean_database(db_connection)
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        assert weights == {}, "Empty database should return empty weights dict"
    
    @pytest.mark.asyncio
    async def test_weights_single_top_agent(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test weights function with only one approved top agent"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create one approved agent with evaluations
        agent_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Test Agent 1",
            score=0.85
        )
        
        # Refresh materialized view to ensure it's up to date
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Should have one agent with full weight (1.0 - dust_weight)
        expected_dust_weight = 1/65535
        expected_top_weight = 1.0 - expected_dust_weight
        
        assert len(weights) == 1
        assert weights[agent_data["miner_hotkey"]] == expected_top_weight
        assert abs(weights[agent_data["miner_hotkey"]] - expected_top_weight) < 0.0001
    
    @pytest.mark.asyncio
    async def test_weights_multiple_approved_agents(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test weights function with multiple approved agents - top agent gets most weight"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create multiple approved agents with different scores
        agent1_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Test Agent 1",
            score=0.90  # Top agent
        )
        
        agent2_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_2",
            agent_name="Test Agent 2",
            score=0.80  # Lower score
        )
        
        agent3_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_3",
            agent_name="Test Agent 3",
            score=0.75  # Lowest score
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Should have 3 agents
        assert len(weights) == 3
        
        # All agents should be present
        assert agent1_data["miner_hotkey"] in weights
        assert agent2_data["miner_hotkey"] in weights
        assert agent3_data["miner_hotkey"] in weights
        
        # Check dust weights for non-top agents
        expected_dust_weight = 1/65535
        assert weights[agent2_data["miner_hotkey"]] == expected_dust_weight
        assert weights[agent3_data["miner_hotkey"]] == expected_dust_weight
        
        # Check top agent weight (should be 1.0 - 3 * dust_weight)
        expected_top_weight = 1.0 - (3 * expected_dust_weight)
        assert abs(weights[agent1_data["miner_hotkey"]] - expected_top_weight) < 0.0001
        
        # Verify total weights sum to 1.0
        total_weight = sum(weights.values())
        assert abs(total_weight - 1.0) < 0.0001
    
    @pytest.mark.asyncio
    async def test_weights_highest_score_wins(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test that the agent with the highest score becomes the top agent"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create two approved agents with different scores
        agent1_data = await self._create_approved_agent_with_evaluations(
            db_connection,
            miner_hotkey="test_hotkey_1",
            agent_name="Test Agent 1",
            score=0.80
        )
        
        agent2_data = await self._create_approved_agent_with_evaluations(
            db_connection,
            miner_hotkey="test_hotkey_2",
            agent_name="Test Agent 2",
            score=0.81  # Higher score
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Agent 2 should be the top agent (higher score)
        expected_dust_weight = 1/65535
        expected_top_weight = 1.0 - (2 * expected_dust_weight)
        
        assert weights[agent2_data["miner_hotkey"]] == expected_top_weight
        assert weights[agent1_data["miner_hotkey"]] == expected_dust_weight
    
    @pytest.mark.asyncio
    async def test_weights_leadership_change(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test that a challenger with a higher score takes leadership"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create two approved agents where the second one has a higher score
        agent1_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_1",
            agent_name="Test Agent 1",
            score=0.80
        )
        
        agent2_data = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="test_hotkey_2",
            agent_name="Test Agent 2",
            score=0.82  # Higher score
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Agent 2 should now be the top agent
        expected_dust_weight = 1/65535
        expected_top_weight = 1.0 - (2 * expected_dust_weight)
        
        assert weights[agent2_data["miner_hotkey"]] == expected_top_weight
        assert weights[agent1_data["miner_hotkey"]] == expected_dust_weight
    
    @pytest.mark.asyncio
    async def test_weights_unapproved_agents_ignored(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test that unapproved agents are not included in weights"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create one approved agent and one unapproved agent
        approved_agent = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="approved_hotkey",
            agent_name="Approved Agent",
            score=0.85,
            approved=True
        )
        
        unapproved_agent = await self._create_approved_agent_with_evaluations(
            db_connection, 
            miner_hotkey="unapproved_hotkey",
            agent_name="Unapproved Agent",
            score=0.90,  # Higher score but unapproved
            approved=False
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Only approved agent should be in weights
        assert len(weights) == 1
        assert approved_agent["miner_hotkey"] in weights
        assert unapproved_agent["miner_hotkey"] not in weights
    
    @pytest.mark.asyncio
    async def test_weights_unapproved_agents_with_evaluations_ignored(self, async_client: AsyncClient, db_connection: asyncpg.Connection):
        """Test that unapproved agents with evaluations are not included in weights"""
        
        # Ensure database is clean first
        await self._clean_database(db_connection)
        
        # Setup: Create an agent with evaluations but not approved
        agent_data = await self._create_approved_agent_with_evaluations(
            db_connection,
            miner_hotkey="unapproved_with_evaluations_hotkey",
            agent_name="Unapproved Agent with Evaluations",
            score=0.95,
            approved=False  # This should exclude it from weights
        )
        
        # Refresh materialized view
        await db_connection.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
        
        # Call the weights endpoint
        response = await async_client.get("/scoring/weights")
        assert response.status_code == 200
        
        weights = response.json()
        
        # Agent should not be in weights (not approved)
        assert len(weights) == 0
        assert agent_data["miner_hotkey"] not in weights
    
    # Helper methods for test setup
    
    async def _clean_database(self, conn: asyncpg.Connection):
        """Clean all test data from database"""
        await conn.execute("DELETE FROM evaluation_runs")
        await conn.execute("DELETE FROM evaluations")
        await conn.execute("DELETE FROM approved_version_ids")
        await conn.execute("DELETE FROM miner_agents")
        await conn.execute("DELETE FROM evaluation_sets")
        await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores")
    
    async def _create_approved_agent_with_evaluations(
        self, 
        conn: asyncpg.Connection, 
        miner_hotkey: str, 
        agent_name: str, 
        score: float,
        approved: bool = True
    ) -> dict:
        """Create an agent with evaluations and optionally approve it"""
        
        # Create evaluation set if it doesn't exist
        await conn.execute("""
            INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) 
            VALUES (1, 'validator', 'test_instance_1')
            ON CONFLICT DO NOTHING
        """)
        
        # Create agent
        version_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, version_id, miner_hotkey, agent_name, 1, datetime.now(timezone.utc), "active")
        
        # Approve agent if requested
        if approved:
            await conn.execute("""
                INSERT INTO approved_version_ids (version_id) VALUES ($1)
            """, version_id)
        
        # Create evaluations with 3 different validators with slightly different scores
        # This ensures that after removing the lowest score, we still have 2+ validators
        evaluation1_id = str(uuid.uuid4())
        evaluation2_id = str(uuid.uuid4())
        evaluation3_id = str(uuid.uuid4())
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, score)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, evaluation1_id, version_id, "validator_1", 1, "completed", datetime.now(timezone.utc), score)
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, score)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, evaluation2_id, version_id, "validator_2", 1, "completed", datetime.now(timezone.utc), score + 0.01)
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at, score)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, evaluation3_id, version_id, "validator_3", 1, "completed", datetime.now(timezone.utc), score + 0.02)
        
        return {
            "version_id": version_id,
            "miner_hotkey": miner_hotkey,
            "agent_name": agent_name,
            "score": score
        }
    
 