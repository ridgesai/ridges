"""
Codegen challenge implementation.

This module defines the CodegenChallenge class for code generation challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from textwrap import dedent
from datetime import datetime, timezone

import httpx
from fiber import Keypair
from logging.logging_utils import get_logger
from fiber.validator import client as validator

from ..base import BaseChallenge, ChallengeType

logger = get_logger(__name__)


@dataclass
class CodegenChallenge(BaseChallenge):
    """
    Code generation challenge.
    
    This challenge type asks miners to generate code based on a problem statement
    and dynamic checklist, using provided context files as reference.
    """
    dynamic_checklist: List[str]
    repository_url: str
    context_file_paths: List[str]  # Relative to repository_url as the repo root
    
    # Legacy fields for backward compatibility (should be removed eventually)
    prompt: str = ""
    model: str = ""
    
    @property
    def challenge_type(self) -> ChallengeType:
        """Return the codegen challenge type."""
        return ChallengeType.CODEGEN
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        return {
            "challenge_id": self.challenge_id,
            "problem_statement": self.problem_statement,
            "dynamic_checklist": self.dynamic_checklist,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "context_file_paths": self.context_file_paths
        }
    
    def get_context_data(self) -> Dict[str, Any]:
        """Return codegen-specific context data."""
        return {
            "dynamic_checklist": self.dynamic_checklist,
            "repository_url": self.repository_url,
            "context_file_paths": self.context_file_paths
        }
    
    def to_detailed_format(self) -> str:
        """
        Format challenge for detailed display.
        
        Returns:
            Formatted string with problem statement, checklist, and context files.
        """
        context_files_string = ""
        for i, file in enumerate(self.context_file_paths):
            context_files_string += f"# File {i} used to solve the problem: {file}\n"
        
        return dedent(f"""
        Problem Statement: {self.problem_statement}
        Checklist of items to consider: {self.dynamic_checklist}
        {context_files_string}
        """).strip()
    
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
        """Send this codegen challenge to a miner node."""
        
        endpoint = "/codegen/challenge"
        payload = self.to_dict()
        
        logger.info(f"Preparing to send codegen challenge to node {node_id}")
        logger.info(f"  Server address: {server_address}")
        logger.info(f"  Hotkey: {hotkey}")
        logger.info(f"  Challenge ID: {self.challenge_id}")
        
        remaining_barriers = 2
        response = None
        
        try:
            # Store the challenge in the database
            if db_manager:
                logger.debug(f"Storing codegen challenge {self.challenge_id} in database")
                db_manager.store_codegen_challenge(self)
            
            # Record the assignment
            if db_manager:
                logger.debug(f"Recording codegen challenge assignment in database")
                db_manager.assign_challenge(self.challenge_id, hotkey, node_id)
            
            # Create client if not provided
            should_close_client = False
            if client is None:
                logger.debug("Creating new HTTP client")
                client = httpx.AsyncClient(timeout=timeout)
                should_close_client = True
            
            if db_manager:
                logger.debug("Marking codegen challenge as sent in database")
                db_manager.mark_challenge_sent(self.challenge_id, hotkey)
            
            if remaining_barriers:
                await barrier.wait()
                remaining_barriers -= 1
            
            try:
                sent_time = datetime.now(timezone.utc)
                logger.debug("Sending codegen challenge request...")
                
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
                        json={"patch": None},
                    )
                
                # Record response details
                received_time = datetime.now(timezone.utc)
                processing_time = (received_time - sent_time).total_seconds()
                
                response.raise_for_status()
                if remaining_barriers:
                    await barrier.wait()
                    remaining_barriers -= 1
                
                logger.debug(f"Got response with status code: {response.status_code}")
                
                # Process the response and store it
                try:
                    response_data = response.json()
                    
                    logger.info(f"Received response for challenge {self.challenge_id}:")
                    logger.info(f"  Processing time: {processing_time:.2f} seconds")
                    
                    # Import here to avoid circular imports
                    from validator.challenge.codegen.response import CodegenResponse
                    
                    # Create CodegenResponse with parsed data
                    codegen_response = CodegenResponse(
                        challenge_id=self.challenge_id,
                        miner_hotkey=hotkey,
                        node_id=node_id,
                        received_at=sent_time,
                        response_patch=response_data.get("patch")
                    )
                    
                    # Store response in responses table
                    if db_manager:
                        logger.debug("Storing response in database")
                        response_id = db_manager.store_response(
                            challenge_id=self.challenge_id,
                            miner_hotkey=hotkey,
                            response=codegen_response,
                            node_id=node_id,
                            received_at=sent_time,
                            completed_at=received_time
                        )
                        logger.info(f"Stored response {response_id} in database")
                    
                    logger.info(f"Challenge {self.challenge_id} sent successfully to {hotkey} (node {node_id})")
                    
                except Exception as e:
                    logger.error("Failed to process response")
                    logger.error(e)
                    logger.error("Full error traceback:", exc_info=True)
                    raise
                
                return response
                
            except Exception as e:
                if remaining_barriers:
                    await barrier.wait()
                    remaining_barriers -= 1
                logger.error(f"Response error: {str(e)}")
                logger.error(f"Response status code: {response.status_code if response else None}")
                logger.error(f"Response headers: {response.headers if response else None}")
                error_msg = f"Failed to send challenge {self.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
                logger.error(error_msg)
                logger.error("Full error traceback:", exc_info=True)
                raise ValueError(error_msg)
            
            finally:
                if should_close_client:
                    logger.debug("Closing HTTP client")
                    await client.aclose()
        
        except Exception as e:
            # Ensure all barriers are released
            while remaining_barriers > 0:
                await barrier.wait()
                remaining_barriers -= 1
            
            error_msg = f"Failed to send challenge {self.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
            logger.error(error_msg)
            logger.error("Full error traceback:", exc_info=True)
            raise ValueError(error_msg) 