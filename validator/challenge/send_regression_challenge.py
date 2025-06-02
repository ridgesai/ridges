"""
Regression challenge: simulates a pull request that introduces a big, i.e. causes any previously-passing test to fail
"""

from datetime import datetime, timezone
from fiber import Keypair
from git import Optional
import httpx
from logging.logging_utils import get_logger
from fiber.validator import client as validator
from validator.challenge.challenge_types import RegressionResponse
from validator.challenge.create_regression_challenge import GeneratedRegressionProblem
from validator.config import CHALLENGE_TIMEOUT
from validator.db.operations import DatabaseManager
from validator.utils.async_utils import AsyncBarrier


logger = get_logger(__name__)


async def send_regression_challenge(
    challenge: GeneratedRegressionProblem,
    server_address: str,
    hotkey: str,
    keypair: Keypair,
    node_id: int,
    barrier: AsyncBarrier,
    db_manager: Optional[DatabaseManager] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = CHALLENGE_TIMEOUT.total_seconds(),  # Use config timeout in seconds
) -> httpx.Response:
    """Send a challenge to a miner node using fiber 2.0.0 protocol."""

    endpoint = "/regression/challenge"
    payload = challenge.to_dict()

    logger.info(f"Preparing to send regression challenge to node {node_id}")
    logger.info(f"  Server address: {server_address}")
    logger.info(f"  Hotkey: {hotkey}")
    logger.info(f"  Challenge ID: {challenge.challenge_id}")

    remaining_barriers = 2
    response = None 

    try:
        # First, store the challenge in the challenges table
        if db_manager:
            logger.debug(f"Storing regression challenge {challenge.challenge_id} in database")
            db_manager.store_regression_challenge(
                challenge=challenge
            )

        # Record the assignment
        if db_manager:
            logger.debug(f"Recording regression challenge assignment in database")
            db_manager.assign_regression_challenge(challenge.challenge_id, hotkey, node_id)

        # Create client if not provided
        should_close_client = False
        if client is None:
            logger.debug("Creating new HTTP client")
            client = httpx.AsyncClient(timeout=timeout)
            should_close_client = True

        if db_manager:
            logger.debug("Marking regression challenge as sent in database")
            db_manager.mark_regression_challenge_sent(challenge.challenge_id, hotkey)
            
        if remaining_barriers:
            await barrier.wait()
            remaining_barriers -= 1
            
        try:
            sent_time = datetime.now(timezone.utc)
            logger.debug("Sending regression challenge request...")
            
            # Send the challenge using fiber validator client with long timeout
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

            except httpx.TimeoutException as e:
                response =  httpx.Response(
                    status_code=200,
                    content=b"",
                    headers={"Content-Type": "application/json"}
                )

            except Exception as e:
                logger.error(f"Error sending regression challenge {challenge.challenge_id}: {str(e)}")
                response =  httpx.Response(
                    status_code=200,
                    content=b"",
                    headers={"Content-Type": "application/json"}
                )

            # Record details about the response sent by the miner 
            received_time = datetime.now(timezone.utc)
            processing_time = (received_time - sent_time).total_seconds()
            # Create response object to track this challenge
            regression_response = RegressionResponse(
                challenge_id=challenge.challenge_id,
                node_id=node_id,
                miner_hotkey=hotkey,
                received_at=received_time,
                response_patch=response.text if response else None
            )

            # Store response in database if manager provided
            if db_manager:
                logger.debug("Storing regression response in database")
                db_manager.store_regression_response(
                    challenge.challenge_id,
                    hotkey,
                    regression_response,
                    node_id,
                    received_at=received_time,
                    completed_at=received_time
                )

            return response

        except Exception as e:
            logger.error(f"Error sending regression challenge {challenge.challenge_id}: {str(e)}")
            if db_manager:
                db_manager.mark_regression_challenge_failed(challenge.challenge_id, hotkey)
            raise
        finally:
            if should_close_client:
                await client.aclose()

    except Exception as e:
        if remaining_barriers: 
            await barrier.wait()
            remaining_barriers -= 1
        if remaining_barriers: 
            await barrier.wait()
            remaining_barriers -= 1
        error_msg = f"Failed to send regression challenge {challenge.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
        logger.error(error_msg)
        logger.error("Full error traceback:", exc_info=True)
        raise ValueError(error_msg)