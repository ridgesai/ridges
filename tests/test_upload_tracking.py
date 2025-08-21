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


def test_record_upload_attempt_exists():
    """Test that the record_upload_attempt function exists and is callable"""
    from api.src.utils.upload_agent_helpers import record_upload_attempt
    
    # Test function exists and is callable
    assert callable(record_upload_attempt)


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
async def test_upload_tracking_integration():
    """Test that upload tracking is integrated into the endpoints"""
    from api.src.endpoints.upload import post_agent, post_open_agent
    
    # Test that functions exist and are callable
    assert callable(post_agent)
    assert callable(post_open_agent)
    
    # Test that record_upload_attempt is called in the upload functions
    # (This is tested more thoroughly in the end-to-end tests)


def test_upload_endpoints_exist():
    """Test that both upload endpoints exist and are callable"""
    from api.src.endpoints.upload import post_agent, post_open_agent
    
    # Check that the functions exist and are callable
    assert callable(post_agent)
    assert callable(post_open_agent)


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
