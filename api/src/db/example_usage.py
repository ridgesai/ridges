"""
Example usage of the SQLAlchemy refactored database operations.

This script demonstrates how the refactored methods work with SQLAlchemy
while maintaining the same interface as the original implementation.
"""

from datetime import datetime
from api.src.utils.models import Agent, AgentVersion
from .operations import DatabaseManager
import uuid

def example_usage():
    """Example demonstrating SQLAlchemy integration"""
    
    # Initialize the database manager (now includes SQLAlchemy)
    db = DatabaseManager()
    
    # Example 1: Create and store an agent (uses SQLAlchemy)
    agent = Agent(
        agent_id=str(uuid.uuid4()),
        miner_hotkey="example_hotkey_123",
        name="Example Agent",
        latest_version=1,
        created_at=datetime.now(),
        last_updated=datetime.now()
    )
    
    print("Storing agent using SQLAlchemy...")
    result = db.store_agent(agent)
    print(f"Store result: {result}")
    
    # Example 2: Retrieve the agent (uses SQLAlchemy)
    print("\nRetrieving agent using SQLAlchemy...")
    retrieved_agent = db.get_agent_by_hotkey("example_hotkey_123")
    if retrieved_agent:
        print(f"Retrieved agent: {retrieved_agent.name}")
    else:
        print("Agent not found")
    
    # Example 3: Create and store an agent version (uses SQLAlchemy)
    agent_version = AgentVersion(
        version_id=str(uuid.uuid4()),
        agent_id=agent.agent_id,
        version_num=1,
        created_at=datetime.now(),
        score=0.85
    )
    
    print("\nStoring agent version using SQLAlchemy...")
    result = db.store_agent_version(agent_version)
    print(f"Store result: {result}")
    
    # Example 4: Get agent count (uses SQLAlchemy)
    print("\nGetting agent count using SQLAlchemy...")
    count = db.get_num_agents()
    print(f"Total agents: {count}")
    
    # Example 5: Store weights (uses SQLAlchemy)
    weights = {
        "hotkey1": 0.4,
        "hotkey2": 0.3,
        "hotkey3": 0.3
    }
    
    print("\nStoring weights using SQLAlchemy...")
    result = db.store_weights(weights)
    print(f"Store result: {result}")
    
    # Example 6: Get latest weights (uses SQLAlchemy)
    print("\nGetting latest weights using SQLAlchemy...")
    latest_weights = db.get_latest_weights()
    if latest_weights:
        print(f"Latest weights: {latest_weights['weights']}")
    
    print("\nAll operations completed successfully!")
    print("Note: Each SQLAlchemy method has a fallback to raw SQL if needed.")

if __name__ == "__main__":
    example_usage()