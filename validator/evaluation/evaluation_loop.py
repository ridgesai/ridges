from pathlib import Path
from datetime import datetime, timezone
import time

from logging.logging_utils import get_logger
from openai import OpenAI
import asyncio

from validator.challenge.challenge_types import GeneratedCodegenProblem
from validator.db.operations import DatabaseManager
from validator.evaluation.evaluation import CodeGenValidator

logger = get_logger(__name__)

async def evaluate_pending_responses(
    validator: CodeGenValidator,
    db_manager: DatabaseManager,
    challenge_id: str
):
    """Evaluate all pending responses for a challenge using the worker pool."""
    try:
        # Fetch the challenge from the DB
        challenge = db_manager.get_challenge(challenge_id)

        if not challenge:
            logger.error(f"Challenge {challenge_id} not found")
            return

        problem = GeneratedCodegenProblem.from_dict(challenge)

        # Fetch pending responses from the DB for a given challenge
        responses = await db_manager.get_pending_responses(challenge_id)

        logger.info(f"Found {len(responses)} responses to challenge {challenge_id}", 1)

        try:
            evaluation_results = await validator.evaluate_responses(problem, responses)
        except Exception as e:
            logger.error(f"Error evaluating responses: {e}")
            db_manager.mark_responses_failed(challenge_id)
            return

        # Update the responses as evaluated and with their score in the DB
        logger.info(f"Updating scores for {len(evaluation_results)} responses on challenge {challenge_id}")

        for response, evaluation in zip(responses, evaluation_results):
            node_id = response.node_id
            response_id = response.response_id
            score = evaluation.score

            logger.info(f"Processing response {response_id} for node {node_id}. Score: {score}")

            db_manager.update_response(
                response_id=response_id,
                score=score,
                evaluated=True,
                evaluated_at=datetime.now(timezone.utc)
            )
        
    except Exception as e:
        logger.error(f"Error in evaluate_pending_responses: {str(e)}")
        await asyncio.sleep(0.5)



async def run_evaluation_loop(
    db_path: Path,
    openai_client: OpenAI,
    validator_hotkey: str,
    sleep_interval: int = 60
) -> None:
    """Entrypoint that sets up the DB, validator, and runs the loop."""
    try: 
        logger.info("Initializing evaluation loop...")
        db_manager = DatabaseManager(db_path)
        validator = CodeGenValidator(db_manager, openai_client, validator_hotkey)
        iteration = 0 

        while True:
            try:
                iteration += 1
                logger.info(f"Starting evaluation loop iteration {iteration}")
                logger.info("Getting database connection...")
                
                # Get pending challenges
                challenge = db_manager.find_challenge_ready_for_evaluation()

                # If no challenges pending eval, sleep for a bit before checking again
                if not challenge:
                    logger.info(f"No challenges ready for evaluation (iteration {iteration})")
                    logger.info(f"Preparing to sleep for {sleep_interval} seconds...")
                    sleep_start = time.time()
                    await asyncio.sleep(sleep_interval)
                    sleep_duration = time.time() - sleep_start
                    logger.info(f"Waking up after sleeping for {sleep_duration:.1f} seconds (iteration {iteration})")
                    continue

                logger.info(f"Processing challenge {challenge[0]} (iteration {iteration})")

                try:
                    # Process the challenge
                    logger.info("Starting evaluate_pending_responses...")
                    await evaluate_pending_responses(
                        validator=validator,
                        db_manager=db_manager,
                        challenge_id=challenge[0]
                    )
                    logger.info(f"Successfully completed challenge processing (iteration {iteration})")
                except Exception as e:
                    logger.error(f"Error processing challenge {challenge} (iteration {iteration}): {str(e)}")
                    logger.error("Stack trace:", exc_info=True)
                
                await asyncio.sleep(sleep_interval)

            except Exception as e:
                logger.error(f"Error in evaluation loop iteration {iteration}: {str(e)}")
                logger.error("Stack trace:", exc_info=True)
                logger.info(f"Preparing to sleep for {sleep_interval} seconds before retry...")
                sleep_start = time.time()
                await asyncio.sleep(sleep_interval)
                sleep_duration = time.time() - sleep_start
                logger.info(f"Waking up after sleeping for {sleep_duration:.1f} seconds to retry after error")
                continue  # Ensure we continue the loop after any error
                

    except Exception as e:
        logger.error(f"Fatal error in run_evaluation_loop: {str(e)}")
        logger.error("Stack trace:", exc_info=True)
        raise  # Re-raise the exception to trigger the task's error callback