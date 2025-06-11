import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass
import time
from shared.logging_utils import get_logger

logger = get_logger(__name__)

@dataclass
class Challenge:
    """Represents a challenge in the queue"""
    challenge_id: str
    validator_hotkey: str
    data: Dict[str, Any]
    received_at: float
    
class ChallengeQueue:
    def __init__(self, max_size: int = 10):
        self.queue = asyncio.Queue(maxsize=max_size)
        self.active_challenges: Dict[str, Challenge] = {}
        self.lock = asyncio.Lock()
        
    async def add_challenge(self, challenge_id: str, validator_hotkey: str, data: Dict[str, Any]) -> bool:
        """Add challenge to queue, return True if successful"""
        try:
            challenge = Challenge(
                challenge_id=challenge_id,
                validator_hotkey=validator_hotkey,
                data=data,
                received_at=time.time()
            )
            
            async with self.lock:
                # Don't add duplicate challenges
                if challenge_id in self.active_challenges:
                    return False
                
                self.active_challenges[challenge_id] = challenge
                
            await self.queue.put(challenge)
            logger.info(f"Added challenge {challenge_id} to queue (queue size: {self.size})")
            return True
        except asyncio.QueueFull:
            logger.warning(f"Failed to add challenge {challenge_id} - queue is full")
            return False
            
    async def get_next_challenge(self) -> Optional[Challenge]:
        """Get the next challenge from the queue"""
        try:
            challenge = await self.queue.get()
            return challenge
        except Exception as e:
            logger.error(f"Error getting next challenge: {str(e)}")
            return None
            
    async def complete_challenge(self, challenge_id: str):
        """Mark a challenge as completed"""
        async with self.lock:
            if challenge_id in self.active_challenges:
                del self.active_challenges[challenge_id]
                logger.info(f"Completed challenge {challenge_id} (remaining active: {len(self.active_challenges)})")
                
    @property
    def is_full(self) -> bool:
        return self.queue.full()
        
    @property
    def size(self) -> int:
        return self.queue.qsize()
        
    @property
    def active_count(self) -> int:
        return len(self.active_challenges) 