from pathlib import Path
from datetime import datetime, timezone
import time
from typing import List

from shared.logging_utils import get_logger, logging_update_active_coroutines, logging_update_eval_loop_num
from openai import OpenAI
import asyncio

from validator.challenge.base import BaseResponse, ValidationResult
from validator.config import CHALLENGE_TIMEOUT
from validator.db.operations import DatabaseManager
from validator.evaluation.graders.trueskill_grader import TrueSkillGrader

logger = get_logger(__name__)

async def evaluate_pending_responses(
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

        # Fetch pending responses from the DB for a given challenge
        responses: List[BaseResponse] = db_manager.get_pending_responses(challenge_id)

        if len(responses) == 0:
            logger.info(f"No responses found for challenge {challenge_id}")
            return
        else:
            logger.info(f"Found {len(responses)} responses for challenge {challenge_id}")

        try:
            grader = TrueSkillGrader(challenge)
            responses_to_test = []

            for response in responses:
                # Preprocess the patch
                response.response_patch = challenge.preprocess_patch(response.response_patch)
                
                # Apply and run tests
                error = challenge.apply_and_run_tests(response.response_patch)
                
                if error is None:
                    logger.info(f"Response {response.response_id} passed testing")
                    responses_to_test.append(response)
                else:
                    logger.info(f"Response {response.response_id} failed because of: {error}")
                    if db_manager:
                        db_manager.mark_response_failed(response.response_id)
            
                # Grade the valid responses and get explanations
                scores = grader.grade(responses_to_test)

                # Return validation results for all responses that passed testing
                evaluation_results = [
                    ValidationResult(
                        is_valid=True,
                        score=scores.get(response.miner_hotkey, 0.0)
                    )
                    for response in responses_to_test
                ]
                logger.info(f"Evaluation results: {evaluation_results}")
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
    sleep_interval: int = 5 * 60 # 5 minutes
) -> None:
    """Entrypoint that sets up the DB and runs the loop."""
    try: 
        logger.info("Initializing evaluation loop...")
        db_manager = DatabaseManager(db_path)
        iteration = 0 

        while True:
            try:
                logging_update_active_coroutines("evaluation_task", True)
                iteration += 1
                logging_update_eval_loop_num(iteration)
                logger.info(f"Starting evaluation loop iteration {iteration}")
                logger.info("Getting database connection...")
                
                # Get pending challenges
                challenge = db_manager.find_challenge_ready_for_evaluation()

                # If no challenges pending eval, sleep for a bit before checking again
                if not challenge:
                    logger.info(f"No challenges ready for evaluation (iteration {iteration})")
                    logger.info(f"Preparing to sleep for {sleep_interval} seconds...")
                    sleep_start = time.time()
                    logging_update_active_coroutines("evaluation_task", False)
                    logging_update_eval_loop_num(0)
                    await asyncio.sleep(sleep_interval)
                    sleep_duration = time.time() - sleep_start
                    logger.info(f"Waking up after sleeping for {sleep_duration:.1f} seconds (iteration {iteration})")
                    continue

                logger.info(f"Processing challenge {challenge.challenge_id} (iteration {iteration})")

                try:
                    # Process the challenge
                    logger.info("Starting evaluate_pending_responses...")
                    await evaluate_pending_responses(
                        db_manager=db_manager,
                        challenge_id=challenge.challenge_id
                    )
                    logger.info(f"Successfully completed challenge processing (iteration {iteration})")
                except Exception as e:
                    logger.error(f"Error processing challenge {challenge.challenge_id} (iteration {iteration}): {str(e)}")
                    logger.error("Stack trace:", exc_info=True)
                
                logging_update_active_coroutines("evaluation_task", False)
                logging_update_eval_loop_num(0)

                # Clean old challenges with no evaluated responses
                db_manager.delete_expired_empty_challenges(timeout_minutes=CHALLENGE_TIMEOUT.total_seconds() / 60) 
                await asyncio.sleep(sleep_interval)

            except Exception as e:
                logger.error(f"Error in evaluation loop iteration {iteration}: {str(e)}")
                logger.error("Stack trace:", exc_info=True)
                logger.info(f"Preparing to sleep for {sleep_interval} seconds before retry...")
                sleep_start = time.time()
                logging_update_active_coroutines("evaluation_task", False)
                logging_update_eval_loop_num(0)
                await asyncio.sleep(sleep_interval)
                sleep_duration = time.time() - sleep_start
                logger.info(f"Waking up after sleeping for {sleep_duration:.1f} seconds to retry after error")
                continue  # Ensure we continue the loop after any error
                

    except Exception as e:
        logger.error(f"Fatal error in run_evaluation_loop: {str(e)}")
        logger.error("Stack trace:", exc_info=True)
        raise  # Re-raise the exception to trigger the task's error callback
