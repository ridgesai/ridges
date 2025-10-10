import time
import random
import asyncio
import pathlib
import traceback
import utils.logger as logger
import validator.config as config

from utils.system_metrics import get_system_metrics
from models.evaluation_run import EvaluationRunStatus
from evaluator.sandbox.sandbox_manager import SandboxManager
from validator.http import get_ridges_platform, post_ridges_platform
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite



# The session ID for this validator
session_id = None



# The sandbox manager and problem suites
sandbox_manager = None
polyglot_suite = None
swebench_verified_suite = None



async def disconnect(reason: str):
    if session_id is None:
        return
    
    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform("/validator/disconnect", {"reason": reason}, bearer_token=session_id)
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error disconnecting validator: {type(e).__name__}: {e}")



async def _send_heartbeat_loop():
    logger.info("Starting send heartbeat loop...")
    while True:
        # logger.info("Sending heartbeat...")
        system_metrics = await get_system_metrics()
        await post_ridges_platform("/validator/heartbeat", {"system_metrics": system_metrics.model_dump()}, bearer_token=session_id, quiet=2)
        await asyncio.sleep(config.SEND_HEARTBEAT_INTERVAL_SECONDS)



async def _run_evaluation_run(evaluation_run):
    evaluation_run_id = evaluation_run['evaluation_run_id']
    problem_name = evaluation_run['problem_name']



    logger.info(f"Starting evaluation run {evaluation_run_id} for problem {problem_name}...")



    MAX_SLEEP_TIME = 1

    await asyncio.sleep(random.random() * MAX_SLEEP_TIME)

    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to initializing_agent...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": EvaluationRunStatus.initializing_agent.value
    }, bearer_token=session_id, quiet=2)

    await asyncio.sleep(random.random() * MAX_SLEEP_TIME)

    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to running_agent...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": EvaluationRunStatus.running_agent.value
    }, bearer_token=session_id, quiet=2)

    await asyncio.sleep(random.random() * MAX_SLEEP_TIME)

    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to initializing_eval...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": EvaluationRunStatus.initializing_eval.value,
        "patch": "FAKE PATCH",
        "agent_logs": "FAKE AGENT LOGS"
    }, bearer_token=session_id, quiet=2)

    await asyncio.sleep(random.random() * MAX_SLEEP_TIME)

    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to running_eval...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": EvaluationRunStatus.running_eval.value
    }, bearer_token=session_id, quiet=2)

    await asyncio.sleep(random.random() * MAX_SLEEP_TIME)

    logger.info(f"Updating evaluation run {evaluation_run_id} for problem {problem_name} to finished...")
    await post_ridges_platform("/validator/update-evaluation-run", {
        "evaluation_run_id": evaluation_run_id,
        "updated_status": EvaluationRunStatus.finished.value,
        "test_results": [{"name": "fake_test", "category": "default", "status": "passed"}],
        "eval_logs": "FAKE EVAL LOGS"
    }, bearer_token=session_id, quiet=2)


    
    logger.info(f"Finished evaluation run {evaluation_run_id} for problem {problem_name}...")



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
        tasks.append(asyncio.create_task(_run_evaluation_run(evaluation_run)))

    await asyncio.gather(*tasks)

    logger.info("Finished evaluation")

    await post_ridges_platform("/validator/finish-evaluation", bearer_token=session_id, quiet=1)



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
    asyncio.create_task(_send_heartbeat_loop())



    # Request an evaluation
    while True:
        logger.info("Requesting an evaluation...")
        
        request_evaluation_response = await post_ridges_platform("/validator/request-evaluation", bearer_token=session_id, quiet=1)

        # If no evaluation is available, wait and try again
        if request_evaluation_response is None:
            logger.info(f"No evaluations available. Waiting for {config.REQUEST_EVALUATION_INTERVAL_SECONDS} seconds...")
            await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)
            continue

        await _run_evaluation(request_evaluation_response)

        await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)



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