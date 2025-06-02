"""
Regression challenge implementation.

This module defines the RegressionChallenge class for regression testing challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import httpx
from fiber import Keypair
from logging.logging_utils import get_logger
from fiber.validator import client as validator

from ..base import BaseChallenge, ChallengeType

logger = get_logger(__name__)


@dataclass
class RegressionChallenge(BaseChallenge):
    """
    Regression testing challenge.
    
    This challenge type simulates a pull request that introduces a bug,
    asking miners to identify and fix issues that cause previously-passing tests to fail.
    """
    repository_url: str
    context_file_paths: List[str]
    
    @property
    def challenge_type(self) -> ChallengeType:
        """Return the regression challenge type."""
        return ChallengeType.REGRESSION
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        return {
            "challenge_id": self.challenge_id,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "problem_statement": self.problem_statement,
            "context_file_paths": self.context_file_paths,
        }
    
    def get_context_data(self) -> Dict[str, Any]:
        """Return regression-specific context data."""
        return {
            "repository_url": self.repository_url,
            "context_file_paths": self.context_file_paths
        }
    
    def get_repository_info(self) -> Dict[str, str]:
        """Get repository-related information."""
        return {
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash or "latest"
        }
    
    def has_context_files(self) -> bool:
        """Check if the challenge has context files."""
        return bool(self.context_file_paths)
    
    def context_file_count(self) -> int:
        """Get the number of context files."""
        return len(self.context_file_paths)
    
    def is_public_repository(self) -> bool:
        """Check if the repository URL appears to be public."""
        # Simple heuristic - check for common public repo patterns
        public_patterns = [
            "github.com",
            "gitlab.com", 
            "bitbucket.org"
        ]
        return any(pattern in self.repository_url.lower() for pattern in public_patterns)
    
    async def send(
        self,
        server_address: str,
        hotkey: str,
        keypair: Keypair,
        node_id: int,
        barrier: "AsyncBarrier",
        db_manager: Optional["DatabaseManager"] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 300.0
    ) -> httpx.Response:
        """Send this regression challenge to a miner node."""
        
        endpoint = "/regression/challenge"
        payload = self.to_dict()
        
        logger.info(f"Preparing to send regression challenge to node {node_id}")
        logger.info(f"  Server address: {server_address}")
        logger.info(f"  Hotkey: {hotkey}")
        logger.info(f"  Challenge ID: {self.challenge_id}")
        
        remaining_barriers = 2
        response = None
        
        try:
            # Store the challenge in the database
            if db_manager:
                logger.debug(f"Storing regression challenge {self.challenge_id} in database")
                db_manager.store_regression_challenge(self)
            
            # Record the assignment
            if db_manager:
                logger.debug(f"Recording regression challenge assignment in database")
                db_manager.assign_regression_challenge(self.challenge_id, hotkey, node_id)
            
            # Create client if not provided
            should_close_client = False
            if client is None:
                logger.debug("Creating new HTTP client")
                client = httpx.AsyncClient(timeout=timeout)
                should_close_client = True
            
            if db_manager:
                logger.debug("Marking regression challenge as sent in database")
                db_manager.mark_regression_challenge_sent(self.challenge_id, hotkey)
            
            if remaining_barriers:
                await barrier.wait()
                remaining_barriers -= 1
            
            try:
                sent_time = datetime.now(timezone.utc)
                logger.debug("Sending regression challenge request...")
                
                # Send the challenge using fiber validator client
                try:
                    response = await validator.make_non_streamed_post(
                        httpx_client=client,
                        server_address=server_address,
                        validator_ss58_address=keypair.ss58_address,
                        miner_ss58_address=hotkey,
                        keypair=keypair,
                        endpoint=endpoint,
                        payload=payload,
                        timeout=timeout
                    )
                except httpx.TimeoutException:
                    response = httpx.Response(
                        status_code=200,
                        content=b"",
                        headers={"Content-Type": "application/json"}
                    )
                except Exception as e:
                    logger.error(f"Error sending regression challenge {self.challenge_id}: {str(e)}")
                    response = httpx.Response(
                        status_code=200,
                        content=b"",
                        headers={"Content-Type": "application/json"}
                    )
                
                # Record response details
                received_time = datetime.now(timezone.utc)
                processing_time = (received_time - sent_time).total_seconds()
                
                # Import here to avoid circular imports
                from validator.challenge.regression.response import RegressionResponse
                
                # Create response object to track this challenge
                regression_response = RegressionResponse(
                    challenge_id=self.challenge_id,
                    node_id=node_id,
                    miner_hotkey=hotkey,
                    received_at=received_time,
                    response_patch=response.text if response else None
                )
                
                # Store response in database if manager provided
                if db_manager:
                    logger.debug("Storing regression response in database")
                    db_manager.store_regression_response(
                        self.challenge_id,
                        hotkey,
                        regression_response,
                        node_id,
                        received_at=received_time,
                        completed_at=received_time
                    )
                
                return response
                
            except Exception as e:
                logger.error(f"Error sending regression challenge {self.challenge_id}: {str(e)}")
                if db_manager:
                    db_manager.mark_regression_challenge_failed(self.challenge_id, hotkey)
                raise
            finally:
                if should_close_client:
                    await client.aclose()
        
        except Exception as e:
            # Ensure all barriers are released
            while remaining_barriers > 0:
                await barrier.wait()
                remaining_barriers -= 1
            
            error_msg = f"Failed to send regression challenge {self.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
            logger.error(error_msg)
            logger.error("Full error traceback:", exc_info=True)
            raise ValueError(error_msg) 