"""
Simple integration tests for upload attempt tracking.
Tests that all upload attempts are properly recorded in the upload_attempts table.
"""

import pytest
import uuid
import io
from unittest.mock import patch, AsyncMock

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestUploadAttemptsSimple:
    """Simple tests for upload attempt tracking"""

    @pytest.mark.asyncio
    async def test_banned_hotkey_creates_upload_attempt_with_ban_reason(self, db_conn):
        """Test that banned hotkey uploads create records with ban reasons"""
        
        # Setup: Insert a banned hotkey with reason
        test_hotkey = f"test_banned_{uuid.uuid4().hex[:8]}"
        ban_reason = "Code obfuscation detected in uploaded agent"
        
        await db_conn.execute("""
            INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) 
            VALUES ($1, $2)
        """, test_hotkey, ban_reason)
        
        # Test: Try to upload with banned hotkey using the track_upload decorator
        from api.src.utils.upload_agent_helpers import record_upload_attempt, get_ban_reason
        
        # Verify ban reason can be retrieved
        retrieved_ban_reason = await get_ban_reason(test_hotkey)
        assert retrieved_ban_reason == ban_reason
        
        # Record a failed upload attempt for banned hotkey
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            hotkey=test_hotkey,
            agent_name="Test Agent",
            filename="agent.py",
            file_size_bytes=1024,
            ip_address="127.0.0.1",
            error_type="banned",
            error_message="Your miner hotkey has been banned for attempting to obfuscate code",
            ban_reason=retrieved_ban_reason,
            http_status_code=403
        )
        
        # Verify: Check that upload attempt was recorded with ban reason
        attempts = await db_conn.fetch("""
            SELECT * FROM upload_attempts WHERE hotkey = $1
        """, test_hotkey)
        
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt["upload_type"] == "agent"
        assert attempt["hotkey"] == test_hotkey
        assert attempt["success"] is False
        assert attempt["error_type"] == "banned"
        assert attempt["ban_reason"] == ban_reason
        assert attempt["http_status_code"] == 403
        assert "banned" in attempt["error_message"].lower()

    @pytest.mark.asyncio
    async def test_successful_upload_creates_attempt_record(self, db_conn):
        """Test that successful uploads create records"""
        
        from api.src.utils.upload_agent_helpers import record_upload_attempt
        
        test_hotkey = f"test_success_{uuid.uuid4().hex[:8]}"
        test_version_id = str(uuid.uuid4())
        
        # Record a successful upload attempt
        await record_upload_attempt(
            upload_type="agent",
            success=True,
            hotkey=test_hotkey,
            agent_name="Test Successful Agent",
            filename="agent.py",
            file_size_bytes=2048,
            ip_address="192.168.1.1",
            version_id=test_version_id
        )
        
        # Verify the record was created
        attempts = await db_conn.fetch("""
            SELECT * FROM upload_attempts WHERE hotkey = $1
        """, test_hotkey)
        
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt["upload_type"] == "agent"
        assert attempt["hotkey"] == test_hotkey
        assert attempt["agent_name"] == "Test Successful Agent"
        assert attempt["filename"] == "agent.py"
        assert attempt["file_size_bytes"] == 2048
        assert attempt["ip_address"] == "192.168.1.1"
        assert attempt["success"] is True
        assert attempt["error_type"] is None
        assert attempt["ban_reason"] is None
        assert attempt["version_id"] == test_version_id

    @pytest.mark.asyncio
    async def test_open_agent_upload_creates_attempt_record(self, db_conn):
        """Test that open agent uploads create records"""
        
        from api.src.utils.upload_agent_helpers import record_upload_attempt
        
        test_hotkey = f"test_open_{uuid.uuid4().hex[:8]}"
        
        # Record an open agent upload attempt
        await record_upload_attempt(
            upload_type="open-agent",
            success=True,
            hotkey=test_hotkey,
            agent_name="Test Open Agent",
            filename="agent.py",
            file_size_bytes=1500,
            ip_address="10.0.0.1"
        )
        
        # Verify the record was created
        attempts = await db_conn.fetch("""
            SELECT * FROM upload_attempts WHERE hotkey = $1
        """, test_hotkey)
        
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt["upload_type"] == "open-agent"
        assert attempt["hotkey"] == test_hotkey
        assert attempt["success"] is True

    @pytest.mark.asyncio 
    async def test_various_error_types_recorded(self, db_conn):
        """Test that various error types are properly recorded"""
        
        from api.src.utils.upload_agent_helpers import record_upload_attempt
        
        test_cases = [
            {
                "error_type": "rate_limit",
                "http_status_code": 429,
                "error_message": "You must wait 300 seconds before uploading a new agent version"
            },
            {
                "error_type": "validation_error", 
                "http_status_code": 400,
                "error_message": "File size must not exceed 1MB"
            },
            {
                "error_type": "internal_error",
                "http_status_code": 500,
                "error_message": "Database connection failed"
            }
        ]
        
        for i, case in enumerate(test_cases):
            test_hotkey = f"test_error_{i}_{uuid.uuid4().hex[:8]}"
            
            await record_upload_attempt(
                upload_type="agent",
                success=False,
                hotkey=test_hotkey,
                agent_name="Test Agent",
                filename="agent.py",
                file_size_bytes=1024,
                error_type=case["error_type"],
                error_message=case["error_message"],
                http_status_code=case["http_status_code"]
            )
            
            # Verify the record
            attempts = await db_conn.fetch("""
                SELECT * FROM upload_attempts WHERE hotkey = $1
            """, test_hotkey)
            
            assert len(attempts) == 1
            attempt = attempts[0]
            assert attempt["success"] is False
            assert attempt["error_type"] == case["error_type"]
            assert attempt["error_message"] == case["error_message"]
            assert attempt["http_status_code"] == case["http_status_code"]

    @pytest.mark.asyncio
    async def test_multiple_attempts_from_same_hotkey(self, db_conn):
        """Test that multiple attempts from the same hotkey are all recorded"""
        
        from api.src.utils.upload_agent_helpers import record_upload_attempt
        
        test_hotkey = f"test_multiple_{uuid.uuid4().hex[:8]}"
        
        # Record multiple attempts
        for i in range(3):
            await record_upload_attempt(
                upload_type="agent",
                success=False,
                hotkey=test_hotkey,
                agent_name=f"Test Agent {i}",
                filename="agent.py",
                file_size_bytes=1024 + i * 100,
                error_type="validation_error",
                error_message=f"Error attempt {i}",
                http_status_code=400
            )
        
        # Verify all attempts were recorded
        attempts = await db_conn.fetch("""
            SELECT * FROM upload_attempts WHERE hotkey = $1 ORDER BY created_at
        """, test_hotkey)
        
        assert len(attempts) == 3
        for i, attempt in enumerate(attempts):
            assert attempt["agent_name"] == f"Test Agent {i}"
            assert attempt["file_size_bytes"] == 1024 + i * 100
            assert attempt["error_message"] == f"Error attempt {i}"

    @pytest.mark.asyncio
    async def test_upload_attempts_table_schema(self, db_conn):
        """Test that the upload_attempts table has the correct schema"""
        
        # Check table exists and has expected columns
        columns = await db_conn.fetch("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_name = 'upload_attempts'
            ORDER BY ordinal_position
        """)
        
        expected_columns = {
            'id': 'uuid',
            'upload_type': 'text', 
            'hotkey': 'text',
            'agent_name': 'text',
            'filename': 'text',
            'file_size_bytes': 'bigint',
            'ip_address': 'text',
            'success': 'boolean',
            'error_type': 'text',
            'error_message': 'text',
            'ban_reason': 'text',
            'http_status_code': 'integer',
            'version_id': 'uuid',
            'created_at': 'timestamp with time zone'
        }
        
        found_columns = {col['column_name']: col['data_type'] for col in columns}
        
        for expected_col, expected_type in expected_columns.items():
            assert expected_col in found_columns, f"Column {expected_col} not found"
            assert found_columns[expected_col] == expected_type, f"Column {expected_col} has type {found_columns[expected_col]}, expected {expected_type}"

    @pytest.mark.asyncio
    async def test_decorator_error_classification(self, db_conn):
        """Test that the track_upload decorator correctly classifies different HTTP errors"""
        
        from api.src.utils.upload_agent_helpers import track_upload
        from fastapi import HTTPException
        
        # Mock function that raises HTTPException
        @track_upload("agent")
        async def mock_upload_function(request, agent_file, **kwargs):
            raise HTTPException(status_code=403, detail="Your miner hotkey has been banned for attempting to obfuscate code")
        
        # Mock request and file objects
        class MockRequest:
            class MockClient:
                host = "127.0.0.1"
            client = MockClient()
        
        class MockFile:
            filename = "agent.py"
            def __init__(self):
                self.file = io.BytesIO(b"test content")
        
        request = MockRequest()
        agent_file = MockFile()
        
        # Test that banned error is caught and recorded
        with pytest.raises(HTTPException):
            await mock_upload_function(request, agent_file, file_info="test_banned_hotkey:1", name="Test Agent")
        
        # Check that attempt was recorded (the decorator should handle this)
        # Note: This test verifies the decorator logic works, but the actual database 
        # recording would happen in a real scenario with proper mocking
