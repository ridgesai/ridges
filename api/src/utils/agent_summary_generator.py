"""
Agent Summary Generator - Integrates with upload workflow to generate AI summaries of agent code
"""
import asyncio
import os
from typing import Optional, Dict, Any
from uuid import UUID

from api.src.backend.db_manager import db_operation, db_transaction
from api.src.backend.queries.agents import get_agent_by_version_id
from api.src.endpoints.retrieval import get_agent_code
from loggers.logging_utils import get_logger
import asyncpg

# Try to import anthropic for direct API calls
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

logger = get_logger(__name__)

async def call_anthropic_direct(messages: list, *, model: str = "claude-sonnet-4-20250514") -> Optional[str]:
    """
    Call Anthropic API directly for testing purposes.
    
    Args:
        messages: List of messages for the AI
        model: Claude model to use
        
    Returns:
        AI response text or None if failed
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("Anthropic library not available")
        return None
        
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY environment variable not set")
            return None
            
        client = anthropic.AsyncAnthropic(api_key=api_key)
        
        # Convert messages to Anthropic format
        system_message = ""
        user_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                user_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Call Anthropic API
        response = await client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0.1,
            system=system_message,
            messages=user_messages
        )
        
        return response.content[0].text.strip()
        
    except Exception as e:
        logger.error(f"Failed to call Anthropic API: {e}")
        return None



AGENT_ANALYSIS_PROMPT = """
You are an expert code analyst specializing in autonomous coding agents. Your task is to analyze agent code and generate a concise, technical summary of how the agent works.

## Analysis Framework

Analyze the provided agent code and identify:

1. **Overall Problem-Solving Strategy**
   - Is it one-shot, iterative, multi-phase, or hybrid?
   - What's the high-level workflow or pipeline?

2. **Key Technical Features**
   - Unique algorithms or techniques (e.g., similarity scoring, memory systems)
   - Advanced capabilities (e.g., test integration, behavioral analysis)
   - Performance optimizations or smart filtering

3. **Tool Usage Patterns**
   - What tools/commands does it use? (file ops, git, search, AI inference)
   - How does it explore and understand the codebase?
   - What's its editing methodology?

4. **Error Handling & Robustness**
   - How does it handle failures or retries?
   - What refinement or validation mechanisms exist?
   - Any fallback strategies?

5. **Code Structure & Flow**
   - What are the main phases or steps?
   - How does it maintain context between operations?
   - Any notable architectural patterns?

## Output Format

Generate a summary following this exact structure:

[Agent Name/Type]
• 1. [What the agent does first - initial step/phase]
• 2. [What it does next - second major step/phase] 
• 3. [Third major step or key process]
• 4. [Final step or how it completes the task]
• Key Feature: [Most important technical capability or unique aspect]
• Robustness: [How it handles errors or edge cases]

## Style Guidelines

- Focus on WORKFLOW and PROCESS FLOW, not just features
- Use numbered steps (1, 2, 3, 4) to show sequence
- Describe the step-by-step journey the agent takes
- Include specific function names when they represent key workflow steps
- Make it readable as a process narrative
- Keep each bullet concise but descriptive of the flow
- Aim for 6 bullet points total following the format above
- No bold formatting within bullet points

## Examples

Hybrid Multi-Phase Problem Solver
• 1. Explores codebase using shell commands (`run_exploration()`) to systematically locate problem areas
• 2. Analyzes current behavior by discovering and running relevant tests via `_discover_relevant_tests()`
• 3. Generates comprehensive understanding through `run_problem_understanding()` with behavioral gap analysis
• 4. Creates focused patches using `run_focused_oneshot()` and refines them through iterative testing
• Key Feature: Conversation memory system retains context across all phases for informed decision-making
• Robustness: Multiple fallback strategies and exploration limits prevent infinite loops or failures

Simple Tool-Based Iterative Agent
• 1. Searches codebase using `search_in_all_files_content()` to find files relevant to the problem statement
• 2. Reads and analyzes file contents with `get_file_content()` to understand the current implementation
• 3. Plans changes by breaking the task into small, manageable edits using structured reasoning
• 4. Applies precise modifications via `apply_code_edit()` with search/replace validation and calls `finish()`
• Key Feature: Structured response format ensures consistent `next_thought` → `next_tool_name` → `next_tool_args` flow
• Robustness: JSON parsing fallbacks and multi-model retry logic handle malformed responses and API failures

Only provide the summary, no other text, or explanation of your thinking.
---

Now analyze the following agent code and generate a summary:

{agent_code}
"""

async def generate_agent_summary(version_id: str, *, proxy_url: str = None, run_id: str) -> Optional[str]:
    """
    Generate an AI summary for an agent version using Anthropic API.
    
    Args:
        version_id: UUID of the agent version
        proxy_url: Not used (kept for compatibility)
        run_id: Run ID for tracking
    
    Returns:
        Generated summary string or None if failed
    """
    try:
        logger.info(f"Generating agent summary for version {version_id}")
        
        # Get agent code from S3
        agent_code = await get_agent_code(version_id, return_as_text=True)
        
        if not agent_code:
            logger.error(f"Could not retrieve agent code for version {version_id}")
            return None
            
        # Prepare prompt with agent code
        prompt = AGENT_ANALYSIS_PROMPT.format(agent_code=agent_code)
        
        # Prepare messages for AI inference
        messages = [
            {
                "role": "system", 
                "content": "You are an expert code analyst. Analyze the provided agent code and generate a concise technical summary following the specified format exactly."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ]
        
        # Call AI inference to generate summary
        logger.info(f"Calling AI inference to analyze agent code ({len(agent_code)} chars)")
        
        # Use Anthropic API directly for summary generation
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        logger.info(f"Debug: ANTHROPIC_AVAILABLE={ANTHROPIC_AVAILABLE}, API_KEY={'SET' if anthropic_key else 'NOT_SET'}")
        
        if not ANTHROPIC_AVAILABLE:
            logger.error("Anthropic package not available - cannot generate summary")
            return None
            
        if not anthropic_key:
            logger.error("Anthropic API key not configured - cannot generate summary")
            return None
            
        logger.info("Using Anthropic API directly for summary generation")
        summary = await call_anthropic_direct(
            messages=messages,
            model="claude-sonnet-4-20250514"  # High-quality analysis model
        )
            
        if not summary:
            logger.error(f"Empty summary generated for version {version_id}")
            return None
            
        logger.info(f"Successfully generated summary for version {version_id} ({len(summary)} chars)")
        return summary
        
    except Exception as e:
        logger.error(f"Failed to generate agent summary for version {version_id}: {e}")
        return None

@db_transaction
async def update_agent_summary(conn: asyncpg.Connection, version_id: str, summary: str) -> bool:
    """
    Update the agent_summary field in the database.
    
    Args:
        conn: Database connection
        version_id: UUID of the agent version
        summary: Generated summary text
    
    Returns:
        True if successful, False otherwise
    """
    try:
        await conn.execute(
            "UPDATE miner_agents SET agent_summary = $1 WHERE version_id = $2",
            summary, version_id
        )
        logger.info(f"Updated agent_summary for version {version_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update agent_summary for version {version_id}: {e}")
        return False

async def generate_and_store_agent_summary(version_id: str, *, proxy_url: str = None, run_id: str) -> bool:
    """
    Complete workflow: generate summary and store it in database.
    
    Args:
        version_id: UUID of the agent version
        proxy_url: Not used (kept for compatibility)
        run_id: Run ID for tracking
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Generate summary
        summary = await generate_agent_summary(
            version_id=version_id,
            proxy_url=proxy_url,
            run_id=run_id
        )
        
        if not summary:
            logger.error(f"Could not generate summary for version {version_id}")
            return False
            
        # Store in database
        success = await update_agent_summary(version_id=version_id, summary=summary)
        
        if success:
            logger.info(f"Successfully generated and stored agent summary for version {version_id}")
            return True
        else:
            logger.error(f"Failed to store agent summary for version {version_id}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to generate and store agent summary for version {version_id}: {e}")
        return False

@db_operation 
async def get_agent_with_summary(conn: asyncpg.Connection, version_id: str) -> Optional[Dict[str, Any]]:
    """
    Get agent details including the summary.
    
    Args:
        conn: Database connection
        version_id: UUID of the agent version
        
    Returns:
        Dict with agent details and summary, or None if not found
    """
    try:
        result = await conn.fetchrow(
            "SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status, agent_summary "
            "FROM miner_agents WHERE version_id = $1",
            version_id
        )
        
        if not result:
            return None
            
        return dict(result)
        
    except Exception as e:
        logger.error(f"Failed to get agent with summary for version {version_id}: {e}")
        return None

# Background task helper for async processing
async def process_summary_generation_queue(agent_versions: list, *, proxy_url: str = None, run_id: str):
    """
    Background task to process a list of agents for summary generation.
    
    Args:
        agent_versions: List of version IDs to process
        proxy_url: Not used (kept for compatibility)
        run_id: Run ID for tracking
    """
    try:
        logger.info(f"Processing summary generation queue for {len(agent_versions)} agents")
        
        for version_id in agent_versions:
            logger.info(f"Processing summary generation for version {version_id}")
            
            # Generate summary using Anthropic API
            success = await generate_and_store_agent_summary(
                version_id=version_id,
                run_id=f"{run_id}-{version_id}"
            )
            
            if success:
                logger.info(f"✅ Generated summary for version {version_id}")
            else:
                logger.error(f"❌ Failed to generate summary for version {version_id}")
                
            # Brief pause to avoid overwhelming the API
            await asyncio.sleep(2)
            
    except Exception as e:
        logger.error(f"Error in summary generation queue processing: {e}") 