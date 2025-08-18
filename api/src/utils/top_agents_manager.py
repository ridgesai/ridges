import os
import asyncio
from pathlib import Path
from typing import Optional, List

from api.src.backend.db_manager import db_operation, new_db
from api.src.backend.entities import MinerAgent
from api.src.utils.s3 import S3Manager
from loggers.logging_utils import get_logger
import asyncpg

logger = get_logger(__name__)

# Get the API directory path
API_DIR = Path(__file__).parent.parent.parent
TOP_AGENTS_DIR = API_DIR / "top"

@db_operation
async def get_top_approved_version_ids(conn: asyncpg.Connection, num_agents: int = 5) -> List[str]:
    """
    Get top approved version IDs using the agent_scores materialized view.
    Returns the actual TOP approved agents by score.
    """
    # Get the maximum set_id
    max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    if not max_set_id_result or max_set_id_result['max_set_id'] is None:
        return []
    
    max_set_id = max_set_id_result['max_set_id']
    
    # Use the materialized view for faster lookups
    results = await conn.fetch("""
        SELECT ass.version_id, ass.final_score
        FROM agent_scores ass
        WHERE ass.approved = true AND ass.approved_at <= NOW() AND ass.set_id = $1
        ORDER BY ass.final_score DESC
        LIMIT $2
    """, max_set_id, num_agents)

    return [str(row['version_id']) for row in results]

async def update_top_agents_cache() -> bool:
    """
    Fetch top 5 approved agents and save to /api/top/ folder.
    This is a standalone function for testing - not integrated yet.
    
    Returns:
        bool: True if update was successful, False otherwise
    """
    try:
        logger.info("Starting top agents cache update...")
        
        # Get top 5 approved version IDs directly (since miner_agents table is empty)
        approved_version_ids = await get_top_approved_version_ids(num_agents=5)
        
        if not approved_version_ids:
            logger.warning("No approved version IDs found in database")
            return False
        
        logger.info(f"Found {len(approved_version_ids)} approved version IDs to cache")
        
        # Create S3 manager
        s3_manager = S3Manager()
        
        # Download and save each agent
        for rank, version_id in enumerate(approved_version_ids, 1):
            try:
                rank_dir = TOP_AGENTS_DIR / f"rank_{rank}"
                rank_dir.mkdir(parents=True, exist_ok=True)
                
                # Download agent code from S3
                agent_code = await s3_manager.get_file_text(f"{version_id}/agent.py")
                
                # Save to local file
                agent_file_path = rank_dir / "agent.py"
                with open(agent_file_path, 'w') as f:
                    f.write(agent_code)
                
                logger.info(f"Saved rank {rank} agent (version_id: {version_id}) to {agent_file_path}")
                
            except Exception as e:
                logger.error(f"Failed to download/save rank {rank} agent (version_id: {version_id}): {e}")
                return False
        
        logger.info("Top agents cache update completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update top agents cache: {e}")
        return False

async def get_cached_top_agent_code(rank: int) -> Optional[str]:
    """
    Get cached agent code by rank (1-5).
    
    Args:
        rank: Agent rank (1-5)
    
    Returns:
        str: Agent code if found, None otherwise
    """
    try:
        agent_file_path = TOP_AGENTS_DIR / f"rank_{rank}" / "agent.py"
        
        if not agent_file_path.exists():
            logger.warning(f"No cached agent found for rank {rank}")
            return None
        
        with open(agent_file_path, 'r') as f:
            return f.read()
            
    except Exception as e:
        logger.error(f"Failed to read cached agent for rank {rank}: {e}")
        return None

async def debug_s3_contents():
    """Debug function to explore what's actually in S3"""
    print("\n=== DEBUG: Exploring S3 Contents ===")
    
    s3_manager = S3Manager()
    
    try:
        # List first 50 objects in S3
        print("First 50 objects in S3:")
        objects = await s3_manager.list_objects(max_keys=50)
        
        if not objects:
            print("  No objects found in S3 bucket")
            return
        
        for i, obj in enumerate(objects[:10]):  # Show first 10
            print(f"  {i+1}. {obj['Key']} (size: {obj['Size']} bytes)")
        
        if len(objects) > 10:
            print(f"  ... and {len(objects) - 10} more objects")
        
        # Check for different key patterns
        print(f"\nTotal objects found: {len(objects)}")
        
        # Analyze key patterns
        key_patterns = {}
        for obj in objects:
            key = obj['Key']
            if '/' in key:
                prefix = key.split('/')[0]
                suffix = '/'.join(key.split('/')[1:])
                pattern = f"{prefix}/{suffix}"
                key_patterns[pattern] = key_patterns.get(pattern, 0) + 1
            else:
                key_patterns['no_slash'] = key_patterns.get('no_slash', 0) + 1
        
        print("\nKey patterns found:")
        for pattern, count in key_patterns.items():
            print(f"  {pattern}: {count} objects")
        
    except Exception as e:
        print(f"Error exploring S3: {e}")

async def debug_approved_version_ids_in_s3():
    """Check if approved version IDs exist with different key patterns"""
    print("\n=== DEBUG: Checking Approved Version IDs in S3 ===")
    
    s3_manager = S3Manager()
    
    # Get approved version IDs from database
    async with new_db.acquire() as conn:
        approved_versions = await conn.fetch("SELECT version_id FROM approved_version_ids LIMIT 5")
        version_ids = [str(row['version_id']) for row in approved_versions]
    
    print(f"Checking {len(version_ids)} approved version IDs:")
    
    for i, version_id in enumerate(version_ids, 1):
        print(f"\n{i}. Version ID: {version_id}")
        
        # Try different key patterns
        key_patterns_to_try = [
            f"{version_id}/agent.py",  # Current pattern we're using
            f"{version_id}",           # Just version ID
            f"agent_{version_id}.py",  # Different naming
            f"agents/{version_id}.py", # Different folder structure
            f"{version_id}.py",        # Version ID as filename
        ]
        
        for pattern in key_patterns_to_try:
            exists = await s3_manager.check_object_exists(pattern)
            status = "✅ EXISTS" if exists else "❌ NOT FOUND"
            print(f"  {pattern}: {status}")
        
        # If none found, try searching for any object containing this version_id
        print(f"  Searching for any object containing '{version_id}'...")
        objects = await s3_manager.list_objects(max_keys=1000)
        matches = [obj['Key'] for obj in objects if version_id in obj['Key']]
        
        if matches:
            print(f"  Found {len(matches)} objects containing this version_id:")
            for match in matches[:3]:  # Show first 3 matches
                print(f"    - {match}")
            if len(matches) > 3:
                print(f"    ... and {len(matches) - 3} more")
        else:
            print(f"  No objects found containing '{version_id}'")

async def test_specific_version_id():
    """Test downloading the specific version ID that the user found in S3"""
    print("\n=== TEST: Downloading specific version ID from S3 ===")
    
    # The version ID the user found in S3 console
    test_version_id = "8e34cb30-0322-40ec-9028-d66f240f1905"
    
    s3_manager = S3Manager()
    
    try:
        # Try to download this specific agent
        print(f"Attempting to download: {test_version_id}/agent.py")
        agent_code = await s3_manager.get_file_text(f"{test_version_id}/agent.py")
        
        print(f"✅ SUCCESS! Downloaded agent code:")
        print(f"  - Size: {len(agent_code)} characters")
        print(f"  - First 100 chars: {agent_code[:100]}...")
        
        # Test saving it to a test location
        test_file = TOP_AGENTS_DIR / "test_agent.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(test_file, 'w') as f:
            f.write(agent_code)
        
        print(f"✅ Successfully saved to: {test_file}")
        
        return True
        
    except Exception as e:
        print(f"❌ FAILED to download {test_version_id}: {e}")
        return False

async def test_update():
    """Test function to manually update the top agents cache"""
    print("Testing top agents cache update...")
    
    # Initialize database connection
    await new_db.open()
    
    try:
        # Debug: Check what's in approved_version_ids
        print("\n=== DEBUG: Getting TOP 5 approved agents by COMPUTED SCORE (excluding outliers) from MAX SET_ID ===")
        async with new_db.acquire() as conn:
            # First get max set_id
            max_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
            if not max_set_id_result or max_set_id_result['max_set_id'] is None:
                print("No evaluation sets found")
                return
            
            max_set_id = max_set_id_result['max_set_id']
            print(f"Using max set_id: {max_set_id}")
            
            # Get the top 5 approved agents using the materialized view
            top_agents = await conn.fetch("""
                SELECT 
                    ass.version_id, 
                    ass.miner_hotkey,
                    ass.final_score as computed_score,
                    ass.validator_count as num_scores_used
                FROM agent_scores ass
                WHERE ass.approved = true AND ass.approved_at <= NOW() AND ass.set_id = $1
                ORDER BY ass.final_score DESC
                LIMIT 5
            """, max_set_id)
            
            print(f"TOP 5 approved agents by computed score (excluding outliers):")
            for i, row in enumerate(top_agents, 1):
                print(f"  Rank {i}: {row['version_id']} (score: {row['computed_score']:.4f}, hotkey: {row['miner_hotkey']}, scores used: {row['num_scores_used']})")
            
            # Also check total approved agents
            total_approved = await conn.fetchval("SELECT COUNT(*) FROM approved_version_ids")
            print(f"\nTotal approved version IDs: {total_approved}")
        
        # Test our main cache update function
        print("\n=== TESTING: Top agents cache update ===")
        result = await update_top_agents_cache()
        print(f"Cache update result: {result}")
        
        # Test reading cached files
        print("\n=== TESTING: Reading cached files ===")
        for rank in range(1, 6):
            code = await get_cached_top_agent_code(rank)
            if code:
                print(f"Rank {rank}: {len(code)} characters")
            else:
                print(f"Rank {rank}: No code found")
                
    finally:
        # Clean up database connection
        await new_db.close()

if __name__ == "__main__":
    asyncio.run(test_update()) 