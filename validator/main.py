# TODO ADAM
from validator.set_weights import set_weights_from_mapping

import os
import sys
import time
import httpx
import random
import asyncio
import pathlib
import traceback
import utils.logger as logger
import validator.config as config

from typing import Any, Dict
from api.endpoints.validator_models import *
from models.problem import ProblemTestResultStatus
from utils.git import COMMIT_HASH, reset_local_repo
from evaluator.models import EvaluationRunException
from utils.system_metrics import get_system_metrics
from models.evaluation_set import EvaluationSetProblem
from evaluator.sandbox.sandbox_manager import SandboxManager
from validator.http_utils import get_ridges_platform, post_ridges_platform
from models.evaluation_run import EvaluationRunStatus, EvaluationRunErrorCode
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite, PolyglotSuiteLanguage
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite



# The session ID for this validator
session_id = None
running_agent_timeout_seconds = None
running_eval_timeout_seconds = None
max_evaluation_run_log_size_bytes = None



# The sandbox manager and problem suites
sandbox_manager = None
problem_suites = []



# Disconnect from the Ridges platform (called when the program exits)
async def disconnect(reason: str):
    if session_id is None:
        return
    
    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform("/validator/disconnect", ValidatorDisconnectRequest(reason=reason), bearer_token=session_id)
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error in disconnect(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)



# A loop that sends periodic heartbeats to the Ridges platform
async def send_heartbeat_loop():
    try:
        logger.info("Starting send heartbeat loop...")
        while True:
            logger.info("Sending heartbeat...")
            system_metrics = await get_system_metrics()
            await post_ridges_platform("/validator/heartbeat", ValidatorHeartbeatRequest(system_metrics=system_metrics), bearer_token=session_id, quiet=2)
            await asyncio.sleep(config.SEND_HEARTBEAT_INTERVAL_SECONDS)
    except Exception as e:
        logger.error(f"Error in send_heartbeat_loop(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)

# A loop that periodically sets weights
async def set_weights_loop():
    logger.info("Starting set weights loop...")
    while True:
        weights_mapping = await get_ridges_platform("/scoring/weights", quiet=1)
        
        await set_weights_from_mapping(weights_mapping)

        await asyncio.sleep(config.SET_WEIGHTS_INTERVAL_SECONDS)
        


# Sends an update-evaluation-run request to the Ridges platform. The extra
# parameter is for fields that are not sent in all requests, such as agent_logs
# and eval_logs, which are only sent on some state transitions.
async def update_evaluation_run(evaluation_run_id: UUID, problem_name: str, updated_status: EvaluationRunStatus, extra: Dict[str, Any] = {}):
    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to {updated_status.value}...")
    
    await post_ridges_platform("/validator/update-evaluation-run", ValidatorUpdateEvaluationRunRequest(
        evaluation_run_id=evaluation_run_id,
        updated_status=updated_status,
        **(extra or {})
    ), bearer_token=session_id, quiet=2)



# Truncates a log if required
def truncate_logs_if_required(log: str) -> str:
    if len(log) > max_evaluation_run_log_size_bytes:
        return f"<truncated {len(log) - max_evaluation_run_log_size_bytes} chars>\n\n" + log[-max_evaluation_run_log_size_bytes:]
    return log



# Simulate a run of an evaluation run, useful for testing, set SIMULATE_EVALUATION_RUNS=True in .env
async def _simulate_run_evaluation_run(evaluation_run_id: UUID, problem_name: str):
    logger.info(f"Starting simulated evaluation run {evaluation_run_id} for problem {problem_name}...")



    # Move from pending -> initializing_agent
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_agent)

    # Move from initializing_agent -> running_agent
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_agent)

    # Move from running_agent -> initializing_eval
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_eval, {
        "patch": "FAKE PATCH",
        "agent_logs": "FAKE AGENT LOGS"
    })

    # Move from initializing_eval -> running_eval
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_eval)

    # Move from running_eval -> finished
    await asyncio.sleep(random.random() * config.SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS)
    await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.finished, {
        "test_results": [{"name": "fake_test", "category": "default", "status": f"{ProblemTestResultStatus.PASS.value}"}],
        "eval_logs": "FAKE EVAL LOGS"
    })


    
    logger.info(f"Finished simulated evaluation run {evaluation_run_id} for problem {problem_name}")



# Run an evaluation run
async def _run_evaluation_run(evaluation_run_id: UUID, problem_name: str, agent_code: str):
    try:
        # Figure out what problem suite this problem belongs to
        problem_suite = next((suite for suite in problem_suites if suite.has_problem_name(problem_name)), None)

        # If we don't have a problem suite that supports this problem, mark the evaluation run as errored
        if problem_suite is None:
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.error, {
                "error_code": EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM.value,
                "error_message": f"The problem '{problem_name}' was not found in any problem suite"
            })
            return

        # Get the problem
        problem = problem_suite.get_problem(problem_name)



        logger.info(f"Starting evaluation run {evaluation_run_id} for problem {problem_name}...")



        try:
            # Move from pending -> initializing_agent
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_agent)

            # Start initializing the agent sandbox
            agent_sandbox = await asyncio.to_thread(
                problem_suite.initialize_agent_sandbox,
                sandbox_manager,
                problem,
                evaluation_run_id,
                agent_code,
                include_solution=config.INCLUDE_SOLUTIONS
            )

            # Move from initializing_agent -> running_agent
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_agent)

            # Start running the agent sandbox
            patch, agent_logs = await asyncio.to_thread(
                problem_suite.run_agent_sandbox,
                sandbox_manager,
                agent_sandbox,
                running_agent_timeout_seconds
            )
            logger.info(f"Finished running agent for problem {problem_name}: {len(patch.splitlines())} lines of patch, {len(agent_logs.splitlines())} lines of agent logs")

            # Move from running_agent -> initializing_eval
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_eval, {
                "patch": patch,
                "agent_logs": truncate_logs_if_required(agent_logs)
            })

            # Start initializing the evaluation sandbox
            eval_sandbox = await asyncio.to_thread(
                problem_suite.initialize_eval_sandbox,
                sandbox_manager,
                problem,
                evaluation_run_id,
                patch
            )

            # Move from initializing_eval -> running_eval
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_eval)

            # Start running the evaluation sandbox
            test_results, eval_logs = await asyncio.to_thread(
                problem_suite.run_eval_sandbox,
                sandbox_manager,
                eval_sandbox,
                running_eval_timeout_seconds
            )
            num_passed = sum(1 for test in test_results if test.status == ProblemTestResultStatus.PASS)
            num_failed = sum(1 for test in test_results if test.status == ProblemTestResultStatus.FAIL)
            num_skipped = sum(1 for test in test_results if test.status == ProblemTestResultStatus.SKIP)
            logger.info(f"Finished running evaluation for problem {problem_name}: {len(test_results)} test results ({num_passed} passed, {num_failed} failed, {num_skipped} skipped), {len(eval_logs.splitlines())} lines of eval logs")

            # Move from running_eval -> finished
            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.finished, {
                "test_results": [test.model_dump() for test in test_results],
                "eval_logs": truncate_logs_if_required(eval_logs)
            })

        except EvaluationRunException as e:
            logger.error(f"Evaluation run {evaluation_run_id} for problem {problem_name} errored: {e}")

            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.error, {
                "error_code": e.error_code.value,
                "error_message": e.error_message
            })

        except Exception as e:
            logger.error(f"Evaluation run {evaluation_run_id} for problem {problem_name} errored: {EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()}: {e}")
            logger.error(traceback.format_exc())

            await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.error, {
                "error_code": EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value,
                "error_message": f"{EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            })



        logger.info(f"Finished evaluation run {evaluation_run_id} for problem {problem_name}")

    except Exception as e:
        logger.error(f"Error in _run_evaluation_run(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)
    


# Run an evaluation, automatically dispatches all runs to either _simulate_run_evaluation_run or _run_evaluation_run
async def _run_evaluation(request_evaluation_response: ValidatorRequestEvaluationResponse):
    logger.info("Received evaluation:")
    logger.info(f"  # of evaluation runs: {len(request_evaluation_response.evaluation_runs)}")

    for evaluation_run in request_evaluation_response.evaluation_runs:
        logger.info(f"    {evaluation_run.problem_name}")



    logger.info("Starting evaluation...")

    tasks = []
    for evaluation_run in request_evaluation_response.evaluation_runs:
        evaluation_run_id = evaluation_run.evaluation_run_id
        problem_name = evaluation_run.problem_name

        if config.SIMULATE_EVALUATION_RUNS:
            tasks.append(asyncio.create_task(_simulate_run_evaluation_run(evaluation_run_id, problem_name)))
        else:
            tasks.append(asyncio.create_task(_run_evaluation_run(evaluation_run_id, problem_name, request_evaluation_response.agent_code)))

    await asyncio.gather(*tasks)

    logger.info("Finished evaluation")

    await post_ridges_platform("/validator/finish-evaluation", ValidatorFinishEvaluationRequest(), bearer_token=session_id, quiet=1)



# Main loop
async def main():
    global session_id
    global running_agent_timeout_seconds
    global running_eval_timeout_seconds
    global max_evaluation_run_log_size_bytes
    global sandbox_manager
    global problem_suites



    # Register with the Ridges platform, yielding us a session ID
    logger.info("Registering validator...")

    try:
        if config.MODE == "validator":
            # Get the current timestamp, and sign it with the validator hotkey
            timestamp = int(time.time())
            signed_timestamp = config.VALIDATOR_HOTKEY.sign(str(timestamp)).hex()
            
            register_response = ValidatorRegistrationResponse(**(await post_ridges_platform("/validator/register-as-validator", ValidatorRegistrationRequest(
                timestamp=timestamp,
                signed_timestamp=signed_timestamp,
                hotkey=config.VALIDATOR_HOTKEY.ss58_address,
                commit_hash=COMMIT_HASH
            ))))
        
        elif config.MODE == "screener":
            register_response = ScreenerRegistrationResponse(**(await post_ridges_platform("/validator/register-as-screener", ScreenerRegistrationRequest(
                name=config.SCREENER_NAME,
                password=config.SCREENER_PASSWORD,
                commit_hash=COMMIT_HASH
            ))))
    
    except httpx.HTTPStatusError as e:
        if config.UPDATE_AUTOMATICALLY and e.response.status_code == 426:
            logger.info("Updating...")
            reset_local_repo(pathlib.Path(__file__).parent.parent, e.response.headers["X-Commit-Hash"])
            sys.exit(0)
        else:
            raise e
    
    session_id = register_response.session_id
    running_agent_timeout_seconds = register_response.running_agent_timeout_seconds
    running_eval_timeout_seconds = register_response.running_eval_timeout_seconds
    max_evaluation_run_log_size_bytes = register_response.max_evaluation_run_log_size_bytes

    logger.info("Registered validator:")
    logger.info(f"  Session ID: {session_id}")
    logger.info(f"  Running Agent Timeout: {running_agent_timeout_seconds} second(s)")
    logger.info(f"  Running Evaluation Timeout: {running_eval_timeout_seconds} second(s)")
    logger.info(f"  Max Evaluation Run Log Size: {max_evaluation_run_log_size_bytes} byte(s)")



    # Create the sandbox manager
    sandbox_manager = SandboxManager(config.RIDGES_INFERENCE_GATEWAY_URL)

    # Load all problem suites
    polyglot_py_suite = PolyglotSuite(PolyglotSuiteLanguage.PYTHON)
    polyglot_js_suite = PolyglotSuite(PolyglotSuiteLanguage.JAVASCRIPT)
    swebench_verified_suite = SWEBenchVerifiedSuite()
    problem_suites = [polyglot_py_suite, polyglot_js_suite, swebench_verified_suite]



    # Get all the problems in the latest set
    latest_set_problems_data = await get_ridges_platform("/evaluation-sets/all-latest-set-problems", quiet=1)
    latest_set_problems = [EvaluationSetProblem(**prob) for prob in latest_set_problems_data]
    latest_set_problem_names = list({prob.problem_name for prob in latest_set_problems})
    
    # Prebuild the images for the SWE-Bench Verified problems
    swebench_verified_suite.prebuild_problem_images(latest_set_problem_names)



    # Start the send heartbeat loop
    asyncio.create_task(send_heartbeat_loop())

    if config.MODE == "validator":
        # Start the set weights loop
        asyncio.create_task(set_weights_loop())



    # Loop forever, just keep requesting evaluations and running them
    while True:
        logger.info("Requesting an evaluation...")
        
        request_evaluation_response_data = await post_ridges_platform("/validator/request-evaluation", ValidatorRequestEvaluationRequest(), bearer_token=session_id, quiet=1)

        # If no evaluation is available, wait and try again
        if request_evaluation_response_data is None:
            logger.info(f"No evaluations available. Waiting for {config.REQUEST_EVALUATION_INTERVAL_SECONDS} seconds...")
            await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)
            continue

        await _run_evaluation(ValidatorRequestEvaluationResponse(**request_evaluation_response_data))



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
        asyncio.run(disconnect("Keyboard interrupt"))
        os._exit(1)
    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        asyncio.run(disconnect(f"Error in main(): {type(e).__name__}: {e}"))
        os._exit(1)