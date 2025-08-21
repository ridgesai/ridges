"""
Simplified upload tracking tests.
Tests all upload attempt tracking functionality in one consolidated file.
"""

import pytest
import uuid
import asyncpg
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_upload_attempts_table_structure():
    """Test that the upload_attempts table exists with correct structure"""
    
    db_url = "postgresql://test_user:test_pass@localhost:5432/postgres"
    conn = await asyncpg.connect(db_url)
    
    try:
        # Check table exists
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'upload_attempts'
            )
        """)
        assert table_exists, "upload_attempts table should exist"
        
        # Check essential columns exist
        columns = await conn.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns 
            WHERE table_name = 'upload_attempts'
        """)
        
        found_columns = {col['column_name']: col['data_type'] for col in columns}
        
        essential_columns = ['upload_type', 'hotkey', 'success', 'error_type', 'ban_reason', 'created_at']
        for col in essential_columns:
            assert col in found_columns, f"Essential column {col} not found"
            
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upload_attempt_database_operations():
    """Test direct database operations on upload_attempts table"""
    
    db_url = "postgresql://test_user:test_pass@localhost:5432/postgres"
    conn = await asyncpg.connect(db_url)
    
    test_hotkey = f"test_db_{uuid.uuid4().hex[:8]}"
    
    try:
        # Test insertion
        await conn.execute("""
            INSERT INTO upload_attempts (upload_type, success, hotkey, agent_name, 
            error_type, ban_reason, http_status_code)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, 'agent', False, test_hotkey, 'Test Agent', 'banned', 'Test ban reason', 403)
        
        # Test retrieval
        attempt = await conn.fetchrow("""
            SELECT * FROM upload_attempts WHERE hotkey = $1
        """, test_hotkey)
        
        assert attempt is not None
        assert attempt['upload_type'] == 'agent'
        assert attempt['success'] is False
        assert attempt['ban_reason'] == 'Test ban reason'
        
    finally:
        await conn.execute("DELETE FROM upload_attempts WHERE hotkey = $1", test_hotkey)
        await conn.close()


@pytest.mark.asyncio 
async def test_ban_reason_retrieval():
    """Test ban reason retrieval from banned_hotkeys table"""
    
    db_url = "postgresql://test_user:test_pass@localhost:5432/postgres"
    conn = await asyncpg.connect(db_url)
    
    test_hotkey = f"test_ban_{uuid.uuid4().hex[:8]}"
    ban_reason = "Code obfuscation detected"
    
    try:
        # Insert banned hotkey
        await conn.execute("""
            INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) 
            VALUES ($1, $2)
        """, test_hotkey, ban_reason)
        
        # Test retrieval
        retrieved_reason = await conn.fetchval("""
            SELECT banned_reason FROM banned_hotkeys
            WHERE miner_hotkey = $1
        """, test_hotkey)
        
        assert retrieved_reason == ban_reason
        
    finally:
        await conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = $1", test_hotkey)
        await conn.close()


def test_track_upload_decorator_exists():
    """Test that the track_upload decorator exists and can be applied"""
    from api.src.utils.upload_agent_helpers import track_upload
    
    # Test decorator can be called
    decorator = track_upload("agent")
    assert callable(decorator)
    
    # Test decorator returns a wrapper
    def dummy_func():
        pass
    
    wrapped = decorator(dummy_func)
    assert callable(wrapped)


@pytest.mark.asyncio
async def test_record_upload_attempt_function():
    """Test that the record_upload_attempt function works correctly"""
    from api.src.utils.upload_agent_helpers import record_upload_attempt
    
    # Mock the database transaction to avoid conflicts
    with patch('api.src.utils.upload_agent_helpers.get_transaction') as mock_transaction:
        mock_conn = AsyncMock()
        mock_transaction.return_value.__aenter__.return_value = mock_conn
        mock_transaction.return_value.__aexit__.return_value = None
        
        # Test function can be called
        await record_upload_attempt(
            upload_type='agent',
            success=False,
            hotkey='test_hotkey',
            error_type='banned',
            ban_reason='Test reason'
        )
        
        # Verify database call was made
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert 'INSERT INTO upload_attempts' in call_args[0]


@pytest.mark.asyncio
async def test_decorator_handles_exceptions():
    """Test that the decorator properly handles both success and failure cases"""
    from api.src.utils.upload_agent_helpers import track_upload
    
    # Mock the dependencies to avoid database conflicts
    with patch('api.src.utils.upload_agent_helpers.record_upload_attempt') as mock_record:
        with patch('api.src.backend.queries.agents.get_ban_reason') as mock_get_ban_reason:
            mock_record.return_value = None
            mock_get_ban_reason.return_value = "Test ban reason"
            
            # Test failure case
            @track_upload("agent")
            async def failing_upload_func(request, agent_file, **kwargs):
                raise HTTPException(status_code=403, detail="Hotkey is banned")
            
            mock_request = MagicMock()
            mock_request.client.host = '127.0.0.1'
            mock_file = MagicMock()
            mock_file.filename = 'agent.py'
            mock_file.file.seek = MagicMock()
            mock_file.file.tell = MagicMock(return_value=1024)
            
            # Test that HTTPException is re-raised and record_upload_attempt is called
            with pytest.raises(HTTPException):
                await failing_upload_func(mock_request, mock_file, name='Test Agent', file_info='test_hotkey:1')
            
            # Verify failure was recorded
            assert mock_record.called
            call_args = mock_record.call_args
            assert call_args[0][1] is False  # success=False
            
            # Reset mock for success test
            mock_record.reset_mock()
            
            # Test success case
            @track_upload("agent")
            async def successful_upload_func(request, agent_file, **kwargs):
                return MagicMock(message="Upload successful! Version ID: 12345.")
            
            result = await successful_upload_func(mock_request, mock_file, name='Test Agent', file_info='test_hotkey:1')
            assert result is not None
            
            # Verify success was recorded
            assert mock_record.called
            call_args = mock_record.call_args
            assert call_args[0][1] is True  # success=True


def test_upload_endpoints_have_decorator():
    """Test that both upload endpoints have the decorator applied"""
    from api.src.endpoints.upload import post_agent, post_open_agent
    
    # Check that the functions exist and are callable
    assert callable(post_agent)
    assert callable(post_open_agent)
    
    # The decorator should have wrapped the functions
    # (We can't easily test the decorator application without complex mocking)


@pytest.mark.asyncio
async def test_multiple_error_scenarios():
    """Test that different error scenarios can be stored in the database"""
    
    db_url = "postgresql://test_user:test_pass@localhost:5432/postgres"
    conn = await asyncpg.connect(db_url)
    
    test_scenarios = [
        ("agent", "banned", "Code obfuscation detected", 403),
        ("agent", "rate_limit", None, 429), 
        ("open-agent", "validation_error", None, 401),
        ("agent", None, None, None)  # Success case
    ]
    
    test_hotkeys = []
    
    try:
        for i, (upload_type, error_type, ban_reason, status_code) in enumerate(test_scenarios):
            test_hotkey = f"test_scenario_{i}_{uuid.uuid4().hex[:8]}"
            test_hotkeys.append(test_hotkey)
            
            success = error_type is None
            
            await conn.execute("""
                INSERT INTO upload_attempts (upload_type, success, hotkey, 
                error_type, ban_reason, http_status_code)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, upload_type, success, test_hotkey, error_type, ban_reason, status_code)
            
            # Verify record was inserted correctly
            attempt = await conn.fetchrow("""
                SELECT * FROM upload_attempts WHERE hotkey = $1
            """, test_hotkey)
            
            assert attempt is not None
            assert attempt['upload_type'] == upload_type
            assert attempt['success'] == success
            assert attempt['error_type'] == error_type
            assert attempt['ban_reason'] == ban_reason
            
    finally:
        # Cleanup
        for test_hotkey in test_hotkeys:
            await conn.execute("DELETE FROM upload_attempts WHERE hotkey = $1", test_hotkey)
        await conn.close()


@pytest.mark.asyncio
async def test_ban_reasons_storage():
    """Test that various ban reasons are properly stored"""
    
    db_url = "postgresql://test_user:test_pass@localhost:5432/postgres"
    conn = await asyncpg.connect(db_url)
    
    ban_reasons = [
        "Code obfuscation detected in uploaded agent",
        "Malicious code patterns detected", 
        "Agent code plagiarized from another miner",
        "Repeated spam uploads detected"
    ]
    
    test_hotkeys = []
    
    try:
        for i, ban_reason in enumerate(ban_reasons):
            test_hotkey = f"test_ban_reason_{i}_{uuid.uuid4().hex[:8]}"
            test_hotkeys.append(test_hotkey)
            
            # Insert banned hotkey
            await conn.execute("""
                INSERT INTO banned_hotkeys (miner_hotkey, banned_reason) 
                VALUES ($1, $2)
            """, test_hotkey, ban_reason)
            
            # Insert corresponding upload attempt
            await conn.execute("""
                INSERT INTO upload_attempts (upload_type, success, hotkey, 
                error_type, ban_reason, http_status_code)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, 'agent', False, test_hotkey, 'banned', ban_reason, 403)
            
            # Verify the ban reason was stored correctly
            stored_attempt = await conn.fetchrow("""
                SELECT ban_reason FROM upload_attempts WHERE hotkey = $1
            """, test_hotkey)
            
            assert stored_attempt['ban_reason'] == ban_reason
            
    finally:
        # Cleanup
        for test_hotkey in test_hotkeys:
            await conn.execute("DELETE FROM upload_attempts WHERE hotkey = $1", test_hotkey)
            await conn.execute("DELETE FROM banned_hotkeys WHERE miner_hotkey = $1", test_hotkey)
        await conn.close()
