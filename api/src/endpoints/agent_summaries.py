import os
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from loggers.logging_utils import get_logger
from loggers.process_tracking import process_context

from api.src.utils.agent_summary_generator import (
    generate_and_store_agent_summary,
    get_agent_with_summary,
    process_summary_generation_queue
)
from api.src.backend.db_manager import get_transaction
from api.src.backend.entities import MinerAgent
from api.src.utils.auth import verify_request_public

logger = get_logger(__name__)
router = APIRouter()

class AdminResponse(BaseModel):
    """Standard admin response"""
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Detailed message about the result")
    data: Optional[dict] = Field(None, description="Additional response data")

class SummaryGenerationRequest(BaseModel):
    """Request model for summary generation"""
    version_id: str = Field(..., description="UUID of the agent version to generate summary for")
    force_regenerate: bool = Field(False, description="Whether to regenerate if summary already exists")

class BackfillRequest(BaseModel):
    """Request model for backfill operations"""
    force_regenerate: bool = Field(False, description="Whether to regenerate all summaries (not just missing ones)")
    limit: Optional[int] = Field(None, description="Maximum number of agents to process (default: all)")
    miner_hotkey: Optional[str] = Field(None, description="Process only agents from this miner hotkey")

@router.post("/generate-summary", response_model=AdminResponse)
async def generate_agent_summary_endpoint(
    request: SummaryGenerationRequest,
    background_tasks: BackgroundTasks,
    admin_password: str
) -> AdminResponse:
    """
    Generate summary for a specific agent version
    
    This endpoint generates (or regenerates) an AI summary for a specific agent version.
    The generation happens in the background for better performance.
    """
    if admin_password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid admin password")
    
    try:
        with process_context("generate_agent_summary"):
            # Check if agent exists
            async with get_transaction() as conn:
                result = await conn.fetchrow("""
                    SELECT version_id, miner_hotkey, agent_name, version_num, created_at, status, agent_summary
                    FROM miner_agents 
                    WHERE version_id = $1
                """, request.version_id)
                
                if not result:
                    raise HTTPException(status_code=404, detail=f"Agent version {request.version_id} not found")
                
                agent = MinerAgent(**dict(result))
                
                # Check if summary already exists
                if agent.agent_summary and not request.force_regenerate:
                    return AdminResponse(
                        status="skipped",
                        message=f"Agent {agent.agent_name} (v{agent.version_num}) already has a summary. Use force_regenerate=true to overwrite.",
                        data={
                            "version_id": request.version_id,
                            "agent_name": agent.agent_name,
                            "version_num": agent.version_num,
                            "has_existing_summary": True
                        }
                    )
                
                # Schedule background summary generation
                background_tasks.add_task(
                    generate_and_store_agent_summary,
                    request.version_id,
                    run_id=f"admin-{datetime.now().isoformat()}"
                )
                
                return AdminResponse(
                    status="success",
                    message=f"Summary generation started for {agent.agent_name} (v{agent.version_num}). Check back in a few minutes.",
                    data={
                        "version_id": request.version_id,
                        "agent_name": agent.agent_name,
                        "version_num": agent.version_num,
                        "background_task_started": True
                    }
                )
                
    except Exception as e:
        logger.error(f"Error generating summary for {request.version_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")

@router.post("/backfill-summaries", response_model=AdminResponse)
async def backfill_agent_summaries_endpoint(
    request: BackfillRequest,
    background_tasks: BackgroundTasks,
    admin_password: str
) -> AdminResponse:
    """
    Backfill agent summaries for multiple agents
    
    This endpoint can:
    - Generate summaries for agents that don't have them yet (force_regenerate=false)
    - Regenerate ALL agent summaries (force_regenerate=true)  
    - Process only agents from a specific miner (miner_hotkey filter)
    - Limit the number of agents processed (limit parameter)
    """
    if admin_password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid admin password")
    
    try:
        with process_context("backfill_agent_summaries"):
            # Build query based on parameters
            query_conditions = []
            query_params = []
            param_count = 0
            
            if not request.force_regenerate:
                query_conditions.append("agent_summary IS NULL")
            
            if request.miner_hotkey:
                param_count += 1
                query_conditions.append(f"miner_hotkey = ${param_count}")
                query_params.append(request.miner_hotkey)
            
            where_clause = "WHERE " + " AND ".join(query_conditions) if query_conditions else ""
            limit_clause = f"LIMIT {request.limit}" if request.limit else ""
            
            query = f"""
                SELECT version_id, miner_hotkey, agent_name, version_num, created_at, agent_summary
                FROM miner_agents 
                {where_clause}
                ORDER BY created_at ASC
                {limit_clause}
            """
            
            async with get_transaction() as conn:
                results = await conn.fetch(query, *query_params)
                
                if not results:
                    return AdminResponse(
                        status="success",
                        message="No agents found matching the criteria",
                        data={
                            "agents_processed": 0,
                            "criteria": {
                                "force_regenerate": request.force_regenerate,
                                "miner_hotkey": request.miner_hotkey,
                                "limit": request.limit
                            }
                        }
                    )
                
                # Schedule background processing
                agent_versions = [str(row['version_id']) for row in results]
                background_tasks.add_task(
                    process_summary_generation_queue,
                    agent_versions,
                    run_id=f"admin-backfill-{datetime.now().isoformat()}"
                )
                
                return AdminResponse(
                    status="success", 
                    message=f"Backfill started for {len(results)} agents. This will run in the background.",
                    data={
                        "agents_to_process": len(results),
                        "background_task_started": True,
                        "criteria": {
                            "force_regenerate": request.force_regenerate,
                            "miner_hotkey": request.miner_hotkey,
                            "limit": request.limit
                        },
                        "sample_agents": [
                            {
                                "version_id": str(row['version_id']),
                                "agent_name": row['agent_name'],
                                "version_num": row['version_num'],
                                "miner_hotkey": row['miner_hotkey'][:12] + "..." if row['miner_hotkey'] else None
                            }
                            for row in results[:5]  # Show first 5 as examples
                        ]
                    }
                )
                
    except Exception as e:
        logger.error(f"Error in backfill operation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start backfill: {str(e)}")

@router.get("/summary-status", response_model=AdminResponse)
async def get_summary_status(
    admin_password: str
) -> AdminResponse:
    """
    Get overview of agent summary status across the platform
    """
    if admin_password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid admin password")
    
    try:
        with process_context("get_summary_status"):
            async with get_transaction() as conn:
                # Get summary statistics
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_agents,
                        COUNT(agent_summary) as agents_with_summaries,
                        COUNT(*) - COUNT(agent_summary) as agents_without_summaries,
                        COUNT(DISTINCT miner_hotkey) as unique_miners
                    FROM miner_agents
                """)
                
                # Get recent agents without summaries
                recent_missing = await conn.fetch("""
                    SELECT version_id, miner_hotkey, agent_name, version_num, created_at
                    FROM miner_agents 
                    WHERE agent_summary IS NULL
                    ORDER BY created_at DESC
                    LIMIT 10
                """)
                
                # Get miners with most missing summaries
                miners_missing = await conn.fetch("""
                    SELECT 
                        miner_hotkey,
                        COUNT(*) as missing_summaries,
                        COUNT(DISTINCT agent_name) as unique_agent_names
                    FROM miner_agents 
                    WHERE agent_summary IS NULL
                    GROUP BY miner_hotkey
                    ORDER BY missing_summaries DESC
                    LIMIT 10
                """)
                
                return AdminResponse(
                    status="success",
                    message="Summary status retrieved successfully",
                    data={
                        "overview": {
                            "total_agents": stats['total_agents'],
                            "agents_with_summaries": stats['agents_with_summaries'],
                            "agents_without_summaries": stats['agents_without_summaries'],
                            "unique_miners": stats['unique_miners'],
                            "completion_percentage": round((stats['agents_with_summaries'] / stats['total_agents']) * 100, 2) if stats['total_agents'] > 0 else 0
                        },
                        "recent_missing_summaries": [
                            {
                                "version_id": str(row['version_id']),
                                "agent_name": row['agent_name'],
                                "version_num": row['version_num'],
                                "miner_hotkey": row['miner_hotkey'][:12] + "..." if row['miner_hotkey'] else None,
                                "created_at": row['created_at'].isoformat()
                            }
                            for row in recent_missing
                        ],
                        "miners_most_missing": [
                            {
                                "miner_hotkey": row['miner_hotkey'][:12] + "..." if row['miner_hotkey'] else None,
                                "missing_summaries": row['missing_summaries'],
                                "unique_agent_names": row['unique_agent_names']
                            }
                            for row in miners_missing
                        ]
                    }
                )
                
    except Exception as e:
        logger.error(f"Error getting summary status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get summary status: {str(e)}")

@router.get("/agent-summary/{version_id}", response_model=AdminResponse, dependencies=[Depends(verify_request_public)])
async def get_agent_summary_endpoint(
    version_id: str
) -> AdminResponse:
    """
    Get detailed information about a specific agent and its summary
    """
    
    try:
        with process_context("get_agent_summary"):
            agent = await get_agent_with_summary(version_id)
            
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent version {version_id} not found")
            
            return AdminResponse(
                status="success",
                message=f"Agent information retrieved for {agent['agent_name']} (v{agent['version_num']})",
                data={
                    "version_id": str(agent['version_id']),
                    "miner_hotkey": agent['miner_hotkey'],
                    "agent_name": agent['agent_name'],
                    "version_num": agent['version_num'],
                    "created_at": agent['created_at'].isoformat(),
                    "status": agent['status'],
                    "has_summary": agent['agent_summary'] is not None,
                    "agent_summary": agent['agent_summary'],
                    "summary_length": len(agent['agent_summary']) if agent['agent_summary'] else 0
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent summary for {version_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get agent summary: {str(e)}") 