'''
Ridges has multiple challenge types. The first is one shot code gen.
We are currently built on Rayon Labs' Fiber + use our synthetic code generation system to send tasks to miners
'''
from typing import Optional
from datetime import datetime, timezone

import httpx
from fiber import Keypair
from fiber.logging_utils import get_logger
from fiber.validator import client as validator

from validator.challenge.challenge_types import CodegenResponse, GeneratedCodegenProblem
from validator.utils.async_utils import AsyncBarrier
from validator.db.operations import DatabaseManager
from validator.config import CHALLENGE_TIMEOUT

logger = get_logger(__name__)

async def send_challenge(
    challenge: GeneratedCodegenProblem,
    server_address: str,
    hotkey: str,
    keypair: Keypair,
    node_id: int,
    barrier: AsyncBarrier,
    db_manager: Optional[DatabaseManager] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = CHALLENGE_TIMEOUT.total_seconds()  # Use config timeout in seconds
) -> httpx.Response:
    """Send a challenge to a miner node using fiber 2.0.0 protocol."""

    endpoint = "/codegen/challenge"
    payload = challenge.to_dict()

    logger.info(f"Preparing to send challenge to node {node_id}")
    logger.info(f"  Server address: {server_address}")
    logger.info(f"  Hotkey: {hotkey}")
    logger.info(f"  Challenge ID: {challenge.challenge_id}")

    remaining_barriers = 2
    response = None 

    try:
        # First, store the challenge in the challenges table
        if db_manager:
            logger.debug(f"Storing challenge {challenge.challenge_id} in database")
            db_manager.store_codegen_challenge(
                challenge=challenge
            )
        
         # Record the assignment
        if db_manager:
            logger.debug(f"Recording challenge assignment in database")
            db_manager.assign_challenge(challenge.challenge_id, hotkey, node_id)

        # Create client if not provided
        should_close_client = False
        if client is None:
            logger.debug("Creating new HTTP client")
            client = httpx.AsyncClient(timeout=timeout)
            should_close_client = True
        
        if db_manager:
            logger.debug("Marking challenge as sent in database")
            db_manager.mark_challenge_sent(challenge.challenge_id, hotkey)
        
        if remaining_barriers:
            await barrier.wait()
            remaining_barriers -= 1
        
        try:
            sent_time = datetime.now(timezone.utc)

            logger.debug("Sending challenge request...")

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
                    json={"patch": None},
                )

            # Record details about the response sent by the miner 
            received_time = datetime.now(timezone.utc)
            processing_time = (received_time - sent_time).total_seconds()

            response.raise_for_status()
            if remaining_barriers: 
                await barrier.wait()
                remaining_barriers -= 1

            logger.debug(f"Got response with status code: {response.status_code}")

            # Process the response and store it in the database
            try:
                response_data = response.json()
                
                # Log essential information about the response
                logger.info(f"Received response for challenge {challenge.challenge_id}:")
                logger.info(f"  Processing time: {processing_time:.2f} seconds")
                                
                # Create CodegenResponse with parsed data
                codegen_response = CodegenResponse(
                    challenge_id=challenge.challenge_id,
                    miner_hotkey=hotkey,
                    node_id=node_id,
                    received_at=sent_time,
                    response_patch=response_data.get("patch")
                )
                
                # Store response in responses table
                if db_manager:
                    logger.debug("Storing response in database")
                    response_id = db_manager.store_response(
                        challenge_id=challenge.challenge_id,
                        miner_hotkey=hotkey,
                        response=codegen_response,
                        node_id=node_id,
                        received_at=sent_time,
                        completed_at=received_time
                    )
                    
                    logger.info(f"Stored response {response_id} in database")
                
                logger.info(f"Challenge {challenge.challenge_id} sent successfully to {hotkey} (node {node_id})")

            except Exception as e:
                logger.error("Failed to process response")
                logger.error(e)
                logger.error("Full error traceback:", exc_info=True)
                raise

            finally:
                return response
        except Exception as e:
            if remaining_barriers: 
                await barrier.wait()
                remaining_barriers -= 1
            logger.error(f"Response error: {str(e)}")
            logger.error(f"Response status code: {response.status_code if response else None}")
            logger.error(f"Response headers: {response.headers if response else None}")
            error_msg = f"Failed to send challenge {challenge.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
            logger.error(error_msg)
            logger.error("Full error traceback:", exc_info=True)
            raise ValueError(error_msg)
            
        finally:
            if should_close_client:
                logger.debug("Closing HTTP client")
                await client.aclose()
    
    except Exception as e:
        if remaining_barriers: 
            await barrier.wait()
            remaining_barriers -= 1
        if remaining_barriers: 
            await barrier.wait()
            remaining_barriers -= 1
        error_msg = f"Failed to send challenge {challenge.challenge_id} to {hotkey} (node {node_id}): {str(e)}"
        logger.error(error_msg)
        logger.error("Full error traceback:", exc_info=True)
        raise ValueError(error_msg)
        

    