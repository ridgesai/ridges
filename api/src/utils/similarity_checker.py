import difflib
from typing import Optional, List, Tuple
from pathlib import Path

from api.src.utils.s3 import S3Manager
from api.src.utils.top_agents_manager import get_cached_top_agent_code, update_top_agents_cache
from loggers.logging_utils import get_logger
from api.src.backend.queries.agents import get_latest_agent

logger = get_logger(__name__)

class SimilarityChecker:
    """
    Checks code similarity between uploaded agent and:
    1. The miner's own previous version
    2. The top 5 approved agents
    """
    
    def __init__(self, similarity_threshold: float = 0.98):
        self.similarity_threshold = similarity_threshold
        self.s3_manager = S3Manager()
    
    def _calculate_similarity(self, code1: str, code2: str) -> float:
        """Calculate similarity ratio between two code strings using difflib"""
        # Remove whitespace and normalize for comparison
        code1_clean = ''.join(code1.split())
        code2_clean = ''.join(code2.split())
        
        # Calculate similarity ratio
        similarity = difflib.SequenceMatcher(None, code1_clean, code2_clean).ratio()
        return similarity
    
    async def check_against_previous_version(self, uploaded_code: str, miner_hotkey: str) -> Optional[Tuple[str, float]]:
        """
        Check if uploaded code is too similar to the miner's previous version.
        
        Returns:
            Tuple of (version_id, similarity_ratio) if too similar, None otherwise
        """
        try:
            latest_agent = await get_latest_agent(miner_hotkey=miner_hotkey)
            
            if not latest_agent:
                logger.info(f"No previous version found for {miner_hotkey}")
                return None
            
            # Get the previous version's code from S3
            previous_code = await self.s3_manager.get_file_text(f"{latest_agent.version_id}/agent.py")
            
            # Calculate similarity
            similarity = self._calculate_similarity(uploaded_code, previous_code)
            
            logger.info(f"Similarity between new upload and previous version {latest_agent.version_id}: {similarity:.4f}")
            
            if similarity >= self.similarity_threshold:
                return (latest_agent.version_id, similarity)
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking similarity against previous version for {miner_hotkey}: {e}")
            # Don't block upload on error - log and continue
            return None
    
    async def check_against_top_agents(self, uploaded_code: str) -> Optional[Tuple[int, float]]:
        """
        Check if uploaded code is too similar to any of the top 5 agents.
        
        Returns:
            Tuple of (rank, similarity_ratio) if too similar, None otherwise
        """
        try:
            # Try to get cached top agents, update if needed
            for rank in range(1, 6):
                cached_code = await get_cached_top_agent_code(rank)
                if not cached_code:
                    logger.info("Top agents cache is empty, updating cache...")
                    cache_updated = await update_top_agents_cache()
                    if not cache_updated:
                        logger.error("Failed to update top agents cache")
                        return None
                    break
            
            # Check against each top agent
            for rank in range(1, 6):
                top_agent_code = await get_cached_top_agent_code(rank)
                
                if not top_agent_code:
                    logger.warning(f"No cached code found for rank {rank}")
                    continue
                
                # Calculate similarity
                similarity = self._calculate_similarity(uploaded_code, top_agent_code)
                
                logger.info(f"Similarity between new upload and top agent rank {rank}: {similarity:.4f}")
                
                if similarity >= self.similarity_threshold:
                    return (rank, similarity)
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking similarity against top agents: {e}")
            # Don't block upload on error - log and continue
            return None
    
    async def validate_upload(self, uploaded_code: str, miner_hotkey: str) -> Tuple[bool, Optional[str]]:
        """
        Validate uploaded code against both previous version and top agents.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check against previous version
        previous_similarity = await self.check_against_previous_version(uploaded_code, miner_hotkey)
        if previous_similarity:
            version_id, similarity = previous_similarity
            error_msg = (
                f"Upload rejected: {similarity:.1%} similarity to your previous version "
                f"({str(version_id)[:8]}...). Minimum required change: {(1-self.similarity_threshold)*100:.1f}%"
            )
            return False, error_msg
        
        # Check against top agents
        top_agent_similarity = await self.check_against_top_agents(uploaded_code)
        if top_agent_similarity:
            rank, similarity = top_agent_similarity
            error_msg = (
                f"Upload rejected: {similarity:.1%} similarity to top agent rank {rank}. "
                f"Minimum required change: {(1-self.similarity_threshold)*100:.1f}%"
            )
            return False, error_msg
        
        return True, None 