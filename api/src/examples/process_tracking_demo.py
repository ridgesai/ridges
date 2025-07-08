#!/usr/bin/env python3
"""
Demo script showing how process tracking works with context variables.
This demonstrates the validator-version flow with automatic process ID tracking.
"""

import asyncio
import json
from src.utils.logging_utils import get_logger
from src.utils.process_tracking import process_context, get_current_process_id
from src.socket.server_helpers import get_relative_version_num, create_evaluations_for_validator, get_next_evaluation

logger = get_logger(__name__)

async def simulate_validator_version_flow():
    """Simulate the complete validator-version flow with process tracking"""
    
    # Simulate validator data
    validator_hotkey = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    version_commit_hash = "abc123def456"
    
    logger.info("=== Starting Validator Version Flow Demo ===")
    
    # This simulates the validator-version event handling
    with process_context("validator-version") as process_id:
        logger.info(f"Processing validator-version event for validator {validator_hotkey}")
        
        # Simulate getting relative version number
        logger.info("Getting relative version number...")
        relative_version = await get_relative_version_num(version_commit_hash)
        
        # Simulate creating evaluations
        logger.info("Creating evaluations for validator...")
        num_evaluations = await create_evaluations_for_validator(validator_hotkey)
        
        # Simulate getting next evaluation
        logger.info("Getting next evaluation...")
        next_eval = await get_next_evaluation(validator_hotkey)
        
        logger.info(f"Validator version flow completed. Process ID: {process_id}")
    
    logger.info("=== Validator Version Flow Demo Completed ===")

async def simulate_multiple_processes():
    """Simulate multiple concurrent processes with different process IDs"""
    
    logger.info("=== Starting Multiple Processes Demo ===")
    
    # Simulate multiple validators connecting simultaneously
    validators = [
        ("validator_1", "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"),
        ("validator_2", "5DAAnrj7VHTznn2HWBqoWwVrqjVoiWzKhpdynUi7XNL6R5Tp"),
        ("validator_3", "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY")
    ]
    
    async def process_validator(name, hotkey):
        with process_context(f"validator-version-{name}") as process_id:
            logger.info(f"Processing {name} with hotkey {hotkey}")
            await asyncio.sleep(0.1)  # Simulate some work
            logger.info(f"Completed processing {name}")
    
    # Run all validators concurrently
    tasks = [process_validator(name, hotkey) for name, hotkey in validators]
    await asyncio.gather(*tasks)
    
    logger.info("=== Multiple Processes Demo Completed ===")

async def demonstrate_context_access():
    """Demonstrate how to access context variables from anywhere in the call stack"""
    
    logger.info("=== Starting Context Access Demo ===")
    
    with process_context("context-demo") as process_id:
        logger.info("Starting context access demonstration")
        
        # Access context variables from anywhere
        current_pid = get_current_process_id()
        
        logger.info(f"Current process ID: {current_pid}")
        
        # Call a function that also logs (will have same process ID)
        await some_nested_function()
        
        logger.info("Context access demonstration completed")
    
    logger.info("=== Context Access Demo Completed ===")

async def some_nested_function():
    """A nested function that demonstrates automatic process ID inheritance"""
    logger.info("This is a nested function call - notice it has the same process ID!")
    
    # Even deeper nesting
    await even_deeper_function()

async def even_deeper_function():
    """An even deeper nested function"""
    logger.info("This is an even deeper nested function - still same process ID!")

async def main():
    """Run all demos"""
    logger.info("Starting Process Tracking Demo")
    
    await simulate_validator_version_flow()
    print("\n" + "="*80 + "\n")
    
    await simulate_multiple_processes()
    print("\n" + "="*80 + "\n")
    
    await demonstrate_context_access()
    
    logger.info("Process Tracking Demo completed")

if __name__ == "__main__":
    asyncio.run(main()) 