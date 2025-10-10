import time
import random
import asyncio
import pathlib
import traceback
import utils.logger as logger
import validator.config as config

from typing import Any, Dict, Optional
from models.problem import ProblemTestResultStatus
from utils.system_metrics import get_system_metrics
from evaluator.sandbox.sandbox_manager import SandboxManager
from evaluator.problem_suites.models import EvaluationRunException
from validator.http import get_ridges_platform, post_ridges_platform
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from models.evaluation_run import EvaluationRunStatus, EvaluationRunErrorCode
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite



# The session ID for this validator
session_id = None



# The sandbox manager and problem suites
sandbox_manager = None
polyglot_suite = None
swebench_verified_suite = None



# Disconnect from the Ridges platform (called when the program exits)
async def disconnect(reason: str):
    if session_id is None:
        return
    
    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform("/validator/disconnect", {"reason": reason}, bearer_token=session_id)
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error disconnecting validator: {type(e).__name__}: {e}")



# A loop that sends periodic heartbeats to the Ridges platform
async def send_heartbeat_loop():
    logger.info("Starting send heartbeat loop...")
    while True:
        # logger.info("Sending heartbeat...")
        system_metrics = await get_system_metrics()
        await post_ridges_platform("/validator/heartbeat", {"system_metrics": system_metrics.model_dump()}, bearer_token=session_id, quiet=2)
        await asyncio.sleep(config.SEND_HEARTBEAT_INTERVAL_SECONDS)



# Sends an update-evaluation-run request to the Ridges platform. The extra
# parameter is for fields that are not sent in all requests, such as agent_logs
# and eval_logs, which are only sent on some state transitions.
async def update_evaluation_run(evaluation_run_id: str, problem_name: str, updated_status: EvaluationRunStatus, extra: Dict[str, Any] = {}):
    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to {updated_status.value}...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": updated_status.value,
        **(extra or {})
    }, bearer_token=session_id, quiet=2)



# Simulate a run of an evaluation run, useful for testing, set SIMULATE_EVALUATION_RUNS=True in .env
async def _simulate_run_evaluation_run(evaluation_run_id: str, problem_name: str):
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


    
    logger.info(f"Finished simulated evaluation run {evaluation_run_id} for problem {problem_name}...")



# Run an evaluation run
async def _run_evaluation_run(evaluation_run_id: str, problem_name: str):
    # Figure out what problem suite this problem belongs to
    problem_suite = None
    if polyglot_suite.has_problem_name(problem_name):
        problem_suite = polyglot_suite
    elif swebench_verified_suite.has_problem_name(problem_name):
        problem_suite = swebench_verified_suite

    # If we don't have a problem suite that supports this problem, mark the evaluation run as errored
    if problem_suite is None:
        update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.error, {
            "error_code": EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM.value,
            "error_message": f"The problem '{problem_name}' was not found in both PolyglotSuite and SWEBenchVerifiedSuite"
        })
        return



    logger.info(f"Starting evaluation run {evaluation_run_id} for problem {problem_name}...")



    try:
        # Move from pending -> initializing_agent
        await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_agent)

        # Start initializing the agent sandbox
        agent_sandbox = await problem_suite.initialize_agent_sandbox(sandbox_manager, problem_name)

        # # Move from initializing_agent -> running_agent
        # await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_agent)

        # # Start running the agent sandbox
        # patch, agent_logs = await problem_suite.run_agent_sandbox(agent_sandbox, problem_name)

        # # Move from running_agent -> initializing_eval
        # await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.initializing_eval, {
        #     "patch": patch,
        #     "agent_logs": agent_logs
        # })

        # # Start initializing the evaluation sandbox
        # eval_sandbox = await problem_suite.initialize_eval_sandbox(sandbox_manager, problem_name)

        # # Move from initializing_eval -> running_eval
        # await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.running_eval)

        # # Start running the evaluation sandbox
        # test_results, eval_logs = await problem_suite.run_eval_sandbox(eval_sandbox, problem_name)

        # # Move from running_eval -> finished
        # await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.finished, {
        #     "test_results": test_results,
        #     "eval_logs": eval_logs
        # })

    except EvaluationRunException as e:
        await update_evaluation_run(evaluation_run_id, problem_name, EvaluationRunStatus.error, {
            "error_code": EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL.value,
            "error_message": f"An error occurred while running the evaluation sandbox: {e}\n\nTraceback:\n{traceback.format_exc()}"
        })
    



    logger.info(f"Finished evaluation run {evaluation_run_id} for problem {problem_name}...")
    


# Run an evaluation, automatically dispatches all runs to either _simulate_run_evaluation_run or _run_evaluation_run
async def _run_evaluation(request_evaluation_response):
    agent_code = request_evaluation_response['agent_code']
    evaluation_runs = request_evaluation_response['evaluation_runs']

    logger.info("Received evaluation:")
    logger.info(f"  # of lines in agent code: {len(agent_code.splitlines())}")
    logger.info(f"  # of evaluation runs: {len(evaluation_runs)}")

    for evaluation_run in evaluation_runs:
        logger.info(f"    {evaluation_run['problem_name']}")



    logger.info("Starting evaluation...")

    tasks = []
    for evaluation_run in evaluation_runs:
        evaluation_run_id = evaluation_run['evaluation_run_id']
        problem_name = evaluation_run['problem_name']

        if config.SIMULATE_EVALUATION_RUNS:
            tasks.append(asyncio.create_task(_simulate_run_evaluation_run(evaluation_run_id, problem_name)))
        else:
            tasks.append(asyncio.create_task(_run_evaluation_run(evaluation_run_id, problem_name)))

    await asyncio.gather(*tasks)

    logger.info("Finished evaluation")

    await post_ridges_platform("/validator/finish-evaluation", bearer_token=session_id, quiet=1)



# Main loop
async def main():
    global session_id
    global sandbox_manager
    global polyglot_suite
    global swebench_verified_suite



    # Register with the Ridges platform, yielding us a session ID
    logger.info("Registering validator...")

    if config.MODE == "validator":
        # Get the current timestamp, and sign it with the validator hotkey
        timestamp = int(time.time())
        signed_timestamp = config.VALIDATOR_HOTKEY.sign(str(timestamp)).hex()
        
        register_response = await post_ridges_platform("/validator/register-as-validator", {
            "timestamp": timestamp,
            "signed_timestamp": signed_timestamp,
            "hotkey": config.VALIDATOR_HOTKEY.ss58_address
        })
    
    elif config.MODE == "screener":
        register_response = await post_ridges_platform("/validator/register-as-screener", {
            "name": config.SCREENER_NAME,
            "password": config.SCREENER_PASSWORD
        })
    
    session_id = register_response["session_id"]

    logger.info(f"Registered validator. Session ID: {session_id}")



    # Create the sandbox manager
    sandbox_manager = SandboxManager(config.RIDGES_INFERENCE_GATEWAY_URL)

    # Load all problem suites
    datasets_path = pathlib.Path(__file__).parent.parent / "evaluator" / "datasets"
    polyglot_suite = PolyglotSuite(datasets_path / "polyglot")
    swebench_verified_suite = SWEBenchVerifiedSuite(datasets_path / "swebench_verified")



    # Get all the problems in the latest set
    latest_set_problems = await get_ridges_platform("/evaluation-sets/all-latest-set-problems", quiet=1)
    latest_set_problem_names = list({prob["problem_name"] for prob in latest_set_problems})
    
    # Prebuild the images for the SWE-Bench Verified problems
    swebench_verified_suite.prebuild_problem_images(latest_set_problem_names)



    # Start the heartbeat loop
    asyncio.create_task(send_heartbeat_loop())



    # Loop forever, just keep requesting evaluations and running them
    while True:
        logger.info("Requesting an evaluation...")
        
        request_evaluation_response = await post_ridges_platform("/validator/request-evaluation", bearer_token=session_id, quiet=1)

        # If no evaluation is available, wait and try again
        if request_evaluation_response is None:
            logger.info(f"No evaluations available. Waiting for {config.REQUEST_EVALUATION_INTERVAL_SECONDS} seconds...")
            await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)
            continue

        await _run_evaluation(request_evaluation_response)



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
        asyncio.run(disconnect("Keyboard interrupt"))
    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        asyncio.run(disconnect("Error in main()"))