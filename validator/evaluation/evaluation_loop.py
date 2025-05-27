from pathlib import Path
from datetime import datetime, timezone
import time

from fiber.logging_utils import get_logger
from openai import OpenAI
import asyncio

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
        # Fetch pending responses from the DB for a given challenge
        responses = await db_manager.get_pending_responses(challenge_id)

        evaluation_results = []

        # For each response, run the validator and get a score,
        for response in responses:
            logger.info(f"Processing response {response.id}")

            try: 
                result = await validator.evaluate_response(response)
                db_manager.get_challenge_assignment_sent_at(challenge_id, response.miner_hotkey)

                evaluation_results.append({
                    "challenge_id": challenge_id,
                    "score": result.score,
                    "error": result.error,
                    **response.to_dict(),
                })

            except Exception as e:
                logger.error(f'Error processing response {response.response_id}')
                db_manager.mark_response_failed(response.response_id)
                continue

        # Update the responses as evaluated and with their score in the DB
        logger.info(f"Updating scores for {len(evaluation_results)} responses")

        for evaluation in evaluation_results:
            node_id = evaluation.get("node_id")
            response_id = evaluation.get("response_id")
            score = evaluation.get("score")

            logger.info(f"Processing response {response_id} for node {node_id}. Score: {score}")

            await db_manager.update_response(
                response_id=response_id,
                score=score,
                evaluated=True,
                evaluated_at=datetime.now(timezone.utc)
            )

    except Exception as e:
        logger.error(f"Error in evaluate_pending_responses: {str(e)}")



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
        validator = CodeGenValidator(openai_client, validator_hotkey)
        validator.db_manager = db_manager
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

                logger.info(f"Processing challenge {challenge['challenge_id']} with {challenge['pending_count']} responses (iteration {iteration})")

                try:
                    # Process the challenge
                    logger.info("Starting evaluate_pending_responses...")
                    await evaluate_pending_responses(
                        validator=validator,
                        db_manager=db_manager,
                        challenge_id=challenge["challenge_id"]
                    )
                    logger.info(f"Successfully completed challenge processing (iteration {iteration})")
                except Exception as e:
                    logger.error(f"Error processing challenge {challenge['challenge_id']} (iteration {iteration}): {str(e)}")
                    logger.error("Stack trace:", exc_info=True)

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