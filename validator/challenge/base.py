"""
Base classes for the challenge system hierarchy.

This module defines the abstract base classes that all challenge types
and responses should inherit from.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from enum import Enum
import time
import asyncio

import httpx
from fiber import Keypair
from shared.logging_utils import get_logger
from fiber.validator import client as validator_client
from validator.utils.clean_patch import remove_comments, remove_docstrings, remove_unused

from validator.db.operations import DatabaseManager
from validator.utils.async_utils import AsyncBarrier

logger = get_logger(__name__)


class ChallengeType(Enum):
    """Enumeration of available challenge types."""
    CODEGEN = "codegen"
    REGRESSION = "regression"


class ValidationResult:
    """Results from challenge validation."""
    def __init__(self, is_valid: bool, score: float = 0.0, feedback: str = ""):
        self.is_valid = is_valid
        self.score = score
        self.feedback = feedback


@dataclass
class BaseChallenge(ABC):
    """
    Abstract base class for all challenge types.
    
    Contains common fields and methods that all challenges should have.
    """
    challenge_id: str
    problem_statement: str
    commit_hash: Optional[str]
    validator_hotkey: str
    
    @property
    def type(self) -> str:
        """Get the type of challenge"""
        raise NotImplementedError("Challenge type must be implemented by subclass")
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        pass
    
    @abstractmethod
    def get_context_data(self) -> Dict[str, Any]:
        """Return challenge-specific context data."""
        pass
    
    def get_endpoint(self) -> str:
        """Get the endpoint for this challenge type"""
        return f"/{self.type}/challenge"
    
    def process_response_data(self, response: httpx.Response) -> Tuple[str, Optional[str]]:
        """
        Process the HTTP response and extract response data.
        
        Args:
            response: The HTTP response from the miner
            
        Returns:
            Tuple of (response_type, response_patch) where response_type 
            indicates the format and response_patch is the actual content
        """
        
        # Default implementation - subclasses can override
        try:
            response_data = response.json()
            return "json", response_data.get("patch")
        except Exception:
            return "text", response.text if response else None
    
    @abstractmethod
    def create_response_object(self, challenge_id: str, hotkey: str, node_id: int, 
                             received_at: datetime, response_patch: Optional[str]):
        """
        Create the appropriate response object for this challenge type.
        
        Args:
            challenge_id: The challenge ID
            hotkey: Miner's hotkey
            node_id: Miner's node ID
            received_at: When the response was received
            response_patch: The response patch content
            
        Returns:
            The response object for this specific challenge type
        """
        pass
    
    async def send(
        self,
        server_address: str,
        hotkey: str,
        keypair: Keypair,
        node_id: int,
        barrier: AsyncBarrier,
        db_manager: 'DatabaseManager',
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 1200.0 # 20 minutes
    ) -> httpx.Response:
        """
        Send this challenge to a miner node.
        
        This is the main template method that handles the common sending logic.
        Subclasses can override specific parts if needed.
        """
        
        endpoint = self.get_endpoint()
        payload = self.to_dict()
        
        logger.info(f"Preparing to send {self.type} challenge to node {node_id}")
        logger.info(f"  Server address: {server_address}")
        logger.info(f"  Hotkey: {hotkey}")
        logger.info(f"  Challenge ID: {self.challenge_id}")
        
        remaining_barriers = 2
        response = None
        
        try:
            # Store the challenge in the database
            if db_manager:
                logger.debug(f"Storing {self.type} challenge {self.challenge_id} in database")
                self.store_in_database(db_manager)
            
            # Record the assignment
            if db_manager:
                logger.debug(f"Recording {self.type} challenge assignment in database")
                db_manager.assign_challenge(self.challenge_id, hotkey, node_id)
            
            # Create client if not provided
            should_close_client = False
            if client is None:
                logger.debug("Creating new HTTP client")
                client = httpx.AsyncClient(timeout=timeout)
                should_close_client = True
            
            if db_manager:
                logger.debug(f"Marking {self.type} challenge as sent in database")
                db_manager.mark_challenge_sent(self.challenge_id, hotkey)
            
            if remaining_barriers:
                await barrier.wait()
                remaining_barriers -= 1
            
            try:
                sent_time = datetime.now(timezone.utc)
                logger.debug(f"Sending {self.type} challenge request...")
                
                # Send the challenge using fiber validator client
                try:
                    response = await validator_client.make_non_streamed_post(
                        httpx_client=client,
                        server_address=server_address,
                        validator_ss58_address=keypair.ss58_address,
                        miner_ss58_address=hotkey,
                        keypair=keypair,
                        endpoint=endpoint,
                        payload=payload,
                        timeout=timeout
                    )
                    
                    # Log raw response for debugging
                    logger.info(f"Raw POST body from miner: {response.text[:500]}{'...' if len(response.text) > 500 else ''}")
                    logger.info(f"POST headers: {dict(response.headers)}")
                    
                    # Attempt to parse JSON and log keys/values
                    try:
                        debug_data = response.json()
                        # Fiber adds a wrapper {"payload": {...}, "signature": "hex"}
                        effective_data = debug_data.get("payload", debug_data)
                        logger.info(f"Parsed JSON keys: {list(effective_data.keys())}")
                        logger.debug(f"Full JSON: {effective_data}")
                    except Exception as json_err:
                        logger.error(f"Failed to parse JSON from miner response: {json_err}")
                    
                    # NEW CODE: Check if this is a queued response
                    if response.status_code == 200:
                        queue_detected = False
                        try:
                            raw_json = response.json()
                            response_data = raw_json.get("payload", raw_json)
                            # Case 1: explicit success marker
                            if response_data.get("success") is True:
                                queue_detected = True
                            # Case 2: patch missing OR empty -> assume queued
                            if ("patch" not in response_data) or (not response_data.get("patch")):
                                queue_detected = True
                            if queue_detected:
                                logger.info(
                                    f"Challenge {self.challenge_id} queued by miner {hotkey}, polling for result"
                                )
                                
                                # Poll miner for the finished result
                                result_endpoint = f"{endpoint}/{self.challenge_id}"
                                start_time = time.time()
                                poll_interval = 10  # seconds
                                max_poll_time = timeout - 60  # Allow some margin before overall timeout

                                while time.time() - start_time < max_poll_time:
                                    logger.info(
                                        f"Polling for challenge {self.challenge_id} result from {hotkey}"
                                    )
                                    try:
                                        poll_response = await validator_client.make_non_streamed_get(
                                            httpx_client=client,
                                            server_address=server_address,
                                            validator_ss58_address=keypair.ss58_address,
                                            miner_ss58_address=hotkey,
                                            keypair=keypair,
                                            endpoint=result_endpoint,
                                            timeout=30.0,
                                        )

                                        # Unwrap Fiber payload if present
                                        poll_raw_json = poll_response.json()
                                        poll_data = poll_raw_json.get("payload", poll_raw_json)

                                        status = poll_data.get("status")

                                        if status == "completed":
                                            logger.info(
                                                f"Challenge {self.challenge_id} completed by miner {hotkey}"
                                            )
                                            response = httpx.Response(
                                                status_code=200,
                                                json={"patch": poll_data.get("patch")},
                                                request=httpx.Request("GET", result_endpoint),
                                            )
                                            break
                                        elif status == "error":
                                            logger.error(
                                                f"Error in challenge {self.challenge_id} from miner {hotkey}: {poll_data.get('error')}"
                                            )
                                            response = httpx.Response(
                                                status_code=200,
                                                json={
                                                    "patch": poll_data.get("patch"),
                                                    "error": poll_data.get("error"),
                                                },
                                                request=httpx.Request("GET", result_endpoint),
                                            )
                                            break
                                        else:
                                            logger.info(
                                                f"Status '{status}' for challenge {self.challenge_id}; sleeping {poll_interval}s before next poll"
                                            )
                                            await asyncio.sleep(poll_interval)
                                    except Exception as poll_error:
                                        logger.error(
                                            f"Error polling for challenge {self.challenge_id} result: {poll_error}"
                                        )
                                        await asyncio.sleep(poll_interval)

                                # Timeout handling
                                if response is None or (
                                    isinstance(response, httpx.Response)
                                    and not response.json().get("patch")
                                ):
                                    logger.error(
                                        f"Polling for challenge {self.challenge_id} result timed out"
                                    )
                                    response = httpx.Response(
                                        status_code=200,
                                        json={"patch": None},
                                        request=httpx.Request("GET", result_endpoint),
                                    )
                        except Exception as parse_error:
                            logger.error(f"Error parsing queue response: {str(parse_error)}")
                            # Continue with normal processing if we can't parse as queue response
                    # END NEW CODE
                    
                except httpx.TimeoutException:
                    # Handle timeout with appropriate default response
                    logger.error(f"Timeout sending {self.type} challenge {self.challenge_id}")
                    response = httpx.Response(
                        status_code=200,
                        json={"patch": None},
                        request=httpx.Request("POST", endpoint)
                    )

                except Exception as e:
                    logger.error(f"Error sending {self.type} challenge {self.challenge_id}: {str(e)}")
                    logger.error("Full error traceback:", exc_info=True)
                    response = httpx.Response(
                        status_code=200,
                        json={"patch": None},
                        request=httpx.Request("POST", endpoint)
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
                    # Extract response data using the appropriate method
                    response_type, response_patch = self.process_response_data(response)
                    
                    logger.info(f"Received response for challenge {self.challenge_id}:")
                    logger.info(f"  Processing time: {processing_time:.2f} seconds")
                    
                    # Create the appropriate response object
                    challenge_response = self.create_response_object(
                        challenge_id=self.challenge_id,
                        hotkey=hotkey,
                        node_id=node_id,
                        received_at=sent_time,
                        response_patch=response_patch
                    )

                    logger.info(f"Challenge response: {challenge_response}")
                    
                    # Store response in responses table
                    if db_manager:
                        response_id = db_manager.store_response(
                            challenge_id=self.challenge_id,
                            miner_hotkey=hotkey,
                            node_id=node_id,
                            response_patch=challenge_response.response_patch,
                            received_at=sent_time,
                            completed_at=received_time,
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
                
                # Mark as failed in database
                if db_manager:
                    db_manager.mark_challenge_failed(self.challenge_id, hotkey)
                
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
    
    def store_in_database(self, db_manager: 'DatabaseManager') -> None:
        """Store this challenge in the database."""
        db_manager.store_challenge(
            challenge_id=self.challenge_id,
            type=self.type,
            challenge_data=self.to_database_dict(),
            validator_hotkey=self.validator_hotkey
        )

    def preprocess_patch(self, patch: str) -> str:
        """
        Preprocesses a patch by removing comments, docstrings, etc.
        
        Args:
            patch: The patch content to preprocess
            
        Returns:
            The preprocessed patch content
        """
        if not patch:
            return ""
        
        without_comments = remove_comments(patch)
        without_docstrings = remove_docstrings(without_comments)
        without_unused = remove_unused(without_docstrings)

        return without_unused.strip()
    
    def apply_and_run_tests(self, patch: str) -> Optional[str]:
        """
        Clones the relevant repo, applies the patch, and runs the tests.
        Also runs pylint and makes sure no new errors have appeared.
        
        Args:
            patch: The patch content to apply and test
            
        Returns:
            An error message if anything fails, otherwise None
        """
        return None

@dataclass
class BaseResponse(ABC):
    """
    Abstract base class for all challenge responses.
    
    Contains common fields and methods that all responses should have.
    """
    challenge_id: str
    node_id: Optional[int] = None
    miner_hotkey: Optional[str] = None
    response_id: Optional[int] = None
    received_at: Optional[datetime] = None
    score: Optional[float] = None
    evaluated: bool = False
    evaluated_at: Optional[datetime] = None
    response_patch: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "challenge_id": self.challenge_id,
            "node_id": self.node_id,
            "miner_hotkey": self.miner_hotkey,
            "response_id": self.response_id,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "score": self.score,
            "evaluated": self.evaluated,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
            "response_patch": self.response_patch
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseResponse':
        """Create response instance from dictionary."""
        received_at = data.get('received_at')
        if received_at and isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        evaluated_at = data.get('evaluated_at')
        if evaluated_at and isinstance(evaluated_at, str):
            evaluated_at = datetime.fromisoformat(evaluated_at)
        
        return cls(
            challenge_id=data['challenge_id'],
            node_id=data.get('node_id'),
            miner_hotkey=data.get('miner_hotkey'),
            response_id=data.get('response_id'),
            received_at=received_at,
            score=data.get('score'),
            evaluated=data.get('evaluated', False),
            evaluated_at=evaluated_at,
            response_patch=data.get('response_patch')
        )

    def is_evaluated(self) -> bool:
        """Check if the response has been evaluated."""
        return self.evaluated

    def has_valid_patch(self) -> bool:
        """Check if the response has a valid patch."""
        return self.response_patch is not None and len(self.response_patch.strip()) > 0

    def validate_response_format(self) -> bool:
        """
        Validate that the response has the expected format.
        
        Returns:
            True if the response format is valid, False otherwise.
        """
        pass

    def get_score(self) -> Optional[float]:
        """
        Get the evaluation score for this response.
        
        Returns:
            The score if evaluated, None otherwise.
        """
        return self.score if self.evaluated else None 