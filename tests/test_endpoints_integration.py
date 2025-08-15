"""
Integration tests for API endpoints with real database testing.
Tests the complete flow from HTTP requests through to database operations.
"""

import pytest
import asyncpg
import os
import uuid
from typing import Optional
from unittest.mock import patch

from httpx import AsyncClient
import pytest_asyncio

# Only set environment variables if they're not already set (don't override GitHub Actions env vars)
if not os.getenv('AWS_MASTER_USERNAME'):
    os.environ.update({
        'AWS_MASTER_USERNAME': 'test_user',
        'AWS_MASTER_PASSWORD': 'test_pass',
        'AWS_RDS_PLATFORM_ENDPOINT': 'localhost',
        'AWS_RDS_PLATFORM_DB_NAME': 'postgres',
        'POSTGRES_TEST_URL': 'postgresql://test_user:test_pass@localhost:5432/postgres'
    })

# Import after setting environment variables
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))

from api.src.main import app


class DatabaseTestSetup:
    """Helper class for database test setup and teardown"""
    
    def __init__(self, test_db_url: str):
        self.test_db_url = test_db_url
        self.pool: Optional[asyncpg.Pool] = None
        
    async def setup_test_database(self):
        """Setup test database schema and data"""
        # Connect to the existing database (don't drop/recreate)
        self.pool = await asyncpg.create_pool(self.test_db_url)
        async with self.pool.acquire() as conn:
            await self._create_schema(conn)
            
    async def _create_schema(self, conn: asyncpg.Connection):
        """Create production schema from postgres_schema.sql"""
        
        # Read the actual production schema file
        schema_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'src', 'backend', 'postgres_schema.sql')
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        # Execute the production schema
        await conn.execute(schema_sql)
        
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
            (1, 'validator', 'test_instance_3')
            ON CONFLICT DO NOTHING
        """)
        
    async def cleanup_test_database(self):
        """Clean up test database"""
        if self.pool:
            await self.pool.close()
            
    def get_connection(self):
        """Get database connection for tests"""
        if not self.pool:
            raise RuntimeError("Test database not initialized")
        return self.pool.acquire()





@pytest_asyncio.fixture
async def async_client():
    """Async HTTP client for testing FastAPI endpoints"""
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


class TestUploadEndpoints:
    """Test agent upload endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_upload_agent_success(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test successful agent upload flow"""
        
        # Mock external dependencies
        with patch('api.src.utils.s3.S3Manager.upload_file_object', return_value="s3://test-bucket/test-agent.py"), \
             patch('api.src.utils.agent_summary_generator.generate_agent_summary'), \
             patch('api.src.utils.upload_agent_helpers.check_code_similarity', return_value=0.3):
            
            # Create test agent data
            agent_data = {
                "miner_hotkey": "test_miner_123",
                "agent_name": "test_agent",
                "code": "def solve_problem(): return 'solution'",
                "signature": "test_signature"
            }
            
            response = await async_client.post("/upload/agent", json=agent_data)
            
            assert response.status_code == 200
            result = response.json()
            assert "version_id" in result
            
            # Verify database state
            agent = await db_conn.fetchrow(
                "SELECT * FROM miner_agents WHERE version_id = $1",
                uuid.UUID(result["version_id"])
            )
            assert agent is not None
            assert agent["miner_hotkey"] == "test_miner_123"
            assert agent["agent_name"] == "test_agent" 
            assert agent["status"] == "awaiting_screening_1"
            
            # Verify evaluation was created
            evaluation = await db_conn.fetchrow(
                "SELECT * FROM evaluations WHERE version_id = $1",
                uuid.UUID(result["version_id"])
            )
            assert evaluation is not None
            assert evaluation["status"] == "waiting"

    @pytest.mark.asyncio
    async def test_upload_agent_banned_hotkey(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test upload rejection for banned hotkey"""
        
        # Insert banned hotkey
        await db_conn.execute(
            "INSERT INTO banned_hotkeys (miner_hotkey) VALUES ($1)",
            "banned_miner"
        )
        
        agent_data = {
            "miner_hotkey": "banned_miner",
            "agent_name": "test_agent",
            "code": "def solve_problem(): return 'solution'",
            "signature": "test_signature"
        }
        
        response = await async_client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 403
        assert "banned" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_agent_rate_limit(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test rate limiting on agent uploads"""
        
        # Insert recent upload
        recent_agent_id = uuid.uuid4()
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at)
            VALUES ($1, $2, $3, 1, NOW() - INTERVAL '1 hour')
        """, recent_agent_id, "rate_limited_miner", "previous_agent")
        
        agent_data = {
            "miner_hotkey": "rate_limited_miner",
            "agent_name": "new_agent",
            "code": "def solve_problem(): return 'solution'",
            "signature": "test_signature"
        }
        
        response = await async_client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 429
        assert "rate limit" in response.json()["detail"].lower()


class TestScoringEndpoints:
    """Test scoring endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_check_top_agent(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test top agent retrieval"""
        
        # Insert test agents with scores
        agent1_id = uuid.uuid4()
        agent2_id = uuid.uuid4()
        
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, 'miner1', 'agent1', 1, NOW(), 'scored'), 
                   ($2, 'miner2', 'agent2', 1, NOW(), 'scored')
        """, agent1_id, agent2_id)
        
        # Insert evaluations with scores  
        eval1_id = uuid.uuid4()
        eval2_id = uuid.uuid4()
        
        await db_conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, score, created_at)
            VALUES ($1, $2, 'validator1', 1, 'completed', 0.85, NOW()),
                   ($3, $4, 'validator1', 1, 'completed', 0.92, NOW())
        """, eval1_id, agent1_id, eval2_id, agent2_id)
        
        # Approve the higher scoring agent
        await db_conn.execute(
            "INSERT INTO approved_version_ids (version_id, set_id) VALUES ($1, 1)",
            agent2_id
        )
        
        # Refresh materialized view
        await db_conn.execute("REFRESH MATERIALIZED VIEW agent_scores")
        
        response = await async_client.get("/scoring/check-top-agent")
        
        assert response.status_code == 200
        result = response.json()
        assert result["miner_hotkey"] == "miner2"
        assert result["avg_score"] == 0.92

    @pytest.mark.asyncio
    async def test_ban_agents(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test agent banning functionality"""
        
        # Insert test agent
        agent_id = uuid.uuid4()
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, 'target_miner', 'target_agent', 1, NOW(), 'scored')
        """, agent_id)
        
        ban_data = {
            "admin_password": "admin_password_123",  # Mock password
            "miner_hotkeys": ["target_miner"]
        }
        
        with patch('api.src.endpoints.scoring.ADMIN_PASSWORD', 'admin_password_123'):
            response = await async_client.post("/scoring/ban-agents", json=ban_data)
            
        assert response.status_code == 200
        
        # Verify ban was applied
        banned = await db_conn.fetchrow(
            "SELECT * FROM banned_hotkeys WHERE miner_hotkey = $1",
            "target_miner"
        )
        assert banned is not None

    @pytest.mark.asyncio
    async def test_approve_version(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test agent version approval"""
        
        # Insert test agent
        agent_id = uuid.uuid4()
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, 'approval_miner', 'approval_agent', 1, NOW(), 'scored')
        """, agent_id)
        
        approval_data = {
            "admin_password": "admin_password_123",
            "version_ids": [str(agent_id)]
        }
        
        with patch('api.src.endpoints.scoring.ADMIN_PASSWORD', 'admin_password_123'):
            response = await async_client.post("/scoring/approve-version", json=approval_data)
            
        assert response.status_code == 200
        
        # Verify approval was applied
        approved = await db_conn.fetchrow(
            "SELECT * FROM approved_version_ids WHERE version_id = $1",
            agent_id
        )
        assert approved is not None


class TestRetrievalEndpoints:
    """Test data retrieval endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_network_stats(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test network statistics retrieval"""
        
        # Insert test data for statistics
        agent1_id = uuid.uuid4()
        agent2_id = uuid.uuid4()
        
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, 'stats_miner1', 'stats_agent1', 1, NOW() - INTERVAL '12 hours', 'scored'),
                   ($2, 'stats_miner2', 'stats_agent2', 1, NOW() - INTERVAL '6 hours', 'scored')
        """, agent1_id, agent2_id)
        
        response = await async_client.get("/retrieval/network-stats")
        
        assert response.status_code == 200
        result = response.json()
        assert "number_of_agents" in result
        assert "agent_iterations_last_24_hours" in result
        assert result["agent_iterations_last_24_hours"] >= 2

    @pytest.mark.asyncio
    async def test_top_agents(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test top agents retrieval"""
        
        # Insert test agents with varying scores
        agents_data = []
        for i in range(5):
            agent_id = uuid.uuid4()
            agents_data.append((agent_id, f'top_miner_{i}', f'top_agent_{i}', i + 1))
            
        await db_conn.executemany("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status)
            VALUES ($1, $2, $3, $4, NOW(), 'scored')
        """, agents_data)
        
        # Insert evaluations with different scores
        for i, (agent_id, _, _, _) in enumerate(agents_data):
            eval_id = uuid.uuid4()
            score = 0.5 + (i * 0.1)  # Increasing scores
            await db_conn.execute("""
                INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, score, created_at)
                VALUES ($1, $2, 'validator1', 1, 'completed', $3, NOW())
            """, eval_id, agent_id, score)
        
        # Refresh materialized view
        await db_conn.execute("REFRESH MATERIALIZED VIEW agent_scores")
        
        response = await async_client.get("/retrieval/top-agents?num_agents=3")
        
        assert response.status_code == 200
        result = response.json()
        assert len(result) <= 3
        
        # Verify ordering (highest score first)
        if len(result) > 1:
            assert result[0]["score"] >= result[1]["score"]

    @pytest.mark.asyncio
    async def test_agent_by_hotkey(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test agent retrieval by hotkey"""
        
        # Insert test agents for same hotkey
        agent1_id = uuid.uuid4()
        agent2_id = uuid.uuid4()
        
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, status, created_at)
            VALUES ($1, 'hotkey_test', 'agent_v1', 1, 'replaced', NOW() - INTERVAL '2 days'),
                   ($2, 'hotkey_test', 'agent_v2', 2, 'scored', NOW() - INTERVAL '1 day')
        """, agent1_id, agent2_id)
        
        response = await async_client.get("/retrieval/agent-by-hotkey?miner_hotkey=hotkey_test")
        
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 2
        assert result[0]["version_num"] == 2  # Latest version first
        assert result[1]["version_num"] == 1


class TestAuthenticationEndpoints:
    """Test authentication endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_open_user_signin(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test open user sign in and registration"""
        
        signin_data = {
            "auth0_user_id": "auth0|test123",
            "email": "test@example.com",
            "name": "Test User",
            "password": "secure_password_123"
        }
        
        response = await async_client.post("/open-users/sign-in", json=signin_data)
        
        assert response.status_code == 200
        result = response.json()
        assert "open_hotkey" in result
        
        # Verify user was created in database
        user = await db_conn.fetchrow(
            "SELECT * FROM open_users WHERE email = $1",
            "test@example.com"
        )
        assert user is not None
        assert user["name"] == "Test User"
        assert user["auth0_user_id"] == "auth0|test123"


class TestAgentSummaryEndpoints:
    """Test agent summary endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_get_agent_summary(self, async_client: AsyncClient, db_conn: asyncpg.Connection):
        """Test agent summary retrieval"""
        
        # Insert agent with summary
        agent_id = uuid.uuid4()
        await db_conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, created_at, status, agent_summary)
            VALUES ($1, 'summary_miner', 'summary_agent', 1, NOW(), 'scored', 'This agent solves coding problems efficiently')
        """, agent_id)
        
        response = await async_client.get(f"/agent-summaries/agent-summary/{agent_id}")
        
        assert response.status_code == 200
        result = response.json()
        assert result["agent_summary"] == "This agent solves coding problems efficiently"
        assert result["version_id"] == str(agent_id)


class TestSystemStatusEndpoints:
    """Test system status endpoints with database integration"""
    
    @pytest.mark.asyncio
    async def test_health_check(self, async_client: AsyncClient, db_setup):
        """Test comprehensive health check"""
        
        # Test basic health check
        response = await async_client.get("/healthcheck")
        assert response.status_code == 200
        assert response.text == '"OK"'
        
        # Test health check results endpoint
        response = await async_client.get("/healthcheck-results")
        assert response.status_code == 200
        result = response.json()
        assert "database_status" in result
        assert "api_status" in result

    @pytest.mark.asyncio
    async def test_status_endpoint(self, async_client: AsyncClient, db_setup):
        """Test detailed system status"""
        
        # Test healthcheck-results endpoint as the status endpoint
        response = await async_client.get("/healthcheck-results")
        
        assert response.status_code == 200
        result = response.json()
        assert "database_status" in result
        assert "api_status" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])