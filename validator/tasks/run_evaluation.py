"""Task for running agents in sandboxes."""

import asyncio
import threading
from datetime import datetime
from pathlib import Path
import json
from typing import TYPE_CHECKING, Any, Dict, List
from ddtrace import tracer
from validator.utils.http_client import get_shared_client

from validator.config import RIDGES_API_URL, RIDGES_PROXY_URL, AGENT_TIMEOUT, EVAL_TIMEOUT
from validator.sandbox.sandbox_manager import SandboxManager
from validator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from validator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

# Global lock to prevent concurrent websocket sends
_websocket_send_lock = threading.Lock()

# this is one of the grossest, nastiest hacks ever written but we need to get this
# out TONIGHT so since it works we will just leave it for now.
# this will be fixed in the next release (within a week)
def safe_websocket_send(websocket_app, message):
    """Safely send websocket message, handling event loop issues and race conditions"""
    def _send_with_lock():
        with _websocket_send_lock:
            asyncio.run(websocket_app.send(message))
    
    try:
        loop = asyncio.get_running_loop()
        # Use the lock even in async context by running in thread
        threading.Thread(target=_send_with_lock, daemon=True).start()
    except RuntimeError:
        # No running loop, run in new thread with lock
        threading.Thread(target=_send_with_lock, daemon=True).start()



# also a gross hack
def run_eval_run(websocket_app, sandbox_manager, polyglot_suite, swebench_verified_suite, agent_code,evaluation_run):
    logger.info(f"XXXXXXXXXX Telling platform run {evaluation_run['run_id']} --> started")
    evaluation_run["status"] = "started"
    safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})

    run_id = evaluation_run["run_id"]
    problem_name = evaluation_run["swebench_instance_id"]



    logger.info(f"Running evaluation for problem {problem_name} with run_id {run_id}")
    # print(evaluation_run)
    
    # Choose the appropriate suite
    suite = polyglot_suite if polyglot_suite.has_problem(problem_name) else swebench_verified_suite

    # This callback will be invoked when the agent finishes running
    def on_agent_finish(agent_result):
        # print(agent_result)
        
        
        if agent_result["status"] == "success":
            logger.info(f"Agent finished successfully for run {run_id}")

            logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> patch_generated")
            evaluation_run["status"] = "patch_generated"
            evaluation_run["response"] = agent_result.get('diff', 'Unknown diff')
            evaluation_run["logs"] = agent_result.get('logs', 'Unknown agent logs')
            evaluation_run["patch_generated_at"] = datetime.now().isoformat()
            safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
            
            # This callback will be invoked when the agent finishes evaluating
            def on_eval_finish(eval_result):
                # print(eval_result)

                concated_logs = (agent_result.get('logs') or 'Unknown agent logs') + '\n' + (eval_result.get('logs') or 'Unknown eval logs')
                print("!!!!!!!!!!!!!!!!! NUMBER OF LINES OF LOGS: ", len(concated_logs.split('\n')))

                if eval_result["status"] == "success":
                    logger.info(f"Evaluation completed successfully for run {run_id}")

                    logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> result_scored")
                    evaluation_run["status"] = "result_scored"
                    
                    evaluation_run["logs"] = concated_logs

                    # store test results
                    # CXII FIXME
                    test_results = eval_result.get("test_results", [])
                    evaluation_run["solved"] = all(t.get("status") == "pass" for t in test_results) if isinstance(test_results, list) else False

                    evaluation_run["result_scored_at"] = datetime.now().isoformat()
                    safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
                else:
                    logger.error(f"Evaluation failed for run {run_id}: {eval_result.get('error', 'Unknown error')}")
                    
                    logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> result_scored")
                    evaluation_run["status"] = "result_scored"
                    evaluation_run["error"] = (agent_result.get('error') or 'Unknown error') + '\n' + (agent_result.get('traceback') or 'Unknown traceback')
                    evaluation_run["logs"] = concated_logs
                    evaluation_run["result_scored_at"] = datetime.now().isoformat()
                    evaluation_run["solved"] = False
                    safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
                    

            # Evaluate the agent
            suite.evaluate_solution_diff(
                sandbox_manager,
                run_id,
                problem_name,
                agent_result["diff"],
                on_eval_finish,
                timeout=EVAL_TIMEOUT
            )

            logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> eval_started")
            evaluation_run["status"] = "eval_started"
            evaluation_run["eval_started_at"] = datetime.now().isoformat()
            safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
        else:
            logger.error(f"Agent failed for run {run_id}: {agent_result.get('error', 'Unknown error')}")
            
            logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> result_scored")
            evaluation_run["status"] = "result_scored"
            evaluation_run["error"] = agent_result.get('error', 'Unknown error') + '\n' + agent_result.get('traceback', 'Unknown traceback')
            evaluation_run["logs"] = agent_result.get('logs', 'Unknown logs')
            evaluation_run["result_scored_at"] = datetime.now().isoformat()
            evaluation_run["solved"] = False
            safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
            
    # Run the agent with the fetched source code
    suite.run_agent_in_sandbox_for_problem(
        sandbox_manager,
        run_id,
        problem_name,
        agent_code,
        on_agent_finish,
        timeout=AGENT_TIMEOUT
        # include_solution=True
    )

    logger.info(f"XXXXXXXXXX Telling platform run {run_id} --> sandbox_created")
    evaluation_run["status"] = "sandbox_created"
    safe_websocket_send(websocket_app, {"event": "update-evaluation-run","evaluation_run": evaluation_run})
    pass



@tracer.wrap(resource="run-evaluation")
async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, version_id: str, evaluation_runs: List[Dict[str, Any]]):
    """Run evaluation for a specific agent version"""
    

    global global_status_running
    global_status_running = True
    # CXII FIXME
    # logger.info(f"Starting evaluation {evaluation_id} for agent {agent_version.miner_hotkey}")

    sandbox_manager = SandboxManager(RIDGES_PROXY_URL)
    polyglot_suite = PolyglotSuite(Path(__file__).parent.parent / "datasets" / "polyglot")
    swebench_verified_suite = SWEBenchVerifiedSuite(Path(__file__).parent.parent / "datasets" / "swebench_verified")
   
    SWEBENCH_VERIFIED_PROBLEMS = [
        "astropy__astropy-13398", "astropy__astropy-13579", "django__django-11138",
        "django__django-11400", "django__django-12325", "django__django-12708",
        "django__django-13128", "django__django-13212", "django__django-13449",
        "django__django-13837", "django__django-14007", "django__django-14011",
        "django__django-14631", "django__django-15268", "django__django-15503",
        "django__django-15629", "django__django-15957", "django__django-16263",
        "django__django-16560", "django__django-16631", "pytest-dev__pytest-5787",
        "pytest-dev__pytest-6197", "pytest-dev__pytest-10356",
        "sphinx-doc__sphinx-9461", "sphinx-doc__sphinx-11510"
    ]
    swebench_verified_suite.prebuild_problem_images(sandbox_manager, SWEBENCH_VERIFIED_PROBLEMS);
   
    #websocket_app.sandbox_manager = sandbox_manager  # Store reference for cancellation
    #errored = False

    try:
       
        async with get_shared_client() as client:
            logger.info(f"Downloading agent code for version {version_id}")
            response = await client.get(
                f"{RIDGES_API_URL}/retrieval/agent-version-file?return_as_text=true",
                params={"version_id": version_id},
            )
            response.raise_for_status()
            agent_code = json.loads(response.content)
            # print(f"Got the agent code: {agent_code}")

            

        
       
        for evaluation_run in evaluation_runs:
            # possible eval run statuses
            # started
            # sandbox_created
            # patch_generated
            # eval_started
            # result_scored
            # cancelled


# {
#   "response": null,

#   "error": null,
#   "logs": null

#   "pass_to_fail_success": null,
#   "fail_to_pass_success": null,
#   "pass_to_pass_success": null,
#   "fail_to_fail_success": null,
#   "solved": null,

#   "status": "started",

#   "sandbox_created_at": null,
#   "patch_generated_at": null,
#   "eval_started_at": null,
#   "result_scored_at": null,
#   "cancelled_at": null,

#   
# 
            run_eval_run(websocket_app, sandbox_manager, polyglot_suite, swebench_verified_suite, agent_code, evaluation_run)


            

        # Start monitoring for platform-side cancellations
        # sandbox_manager.start_cancellation_monitoring()
        
        while sandbox_manager.get_num_sandboxes() > 0:
            await asyncio.sleep(1)

        logger.info(f"Evaluation {evaluation_id} completed successfully")
    except asyncio.CancelledError:
        logger.info(f"Evaluation {evaluation_id} was cancelled")
        # Only treat as error if not cancelled by platform
        errored = not sandbox_manager._cancelled_by_platform
        if sandbox_manager._cancelled_by_platform:
            logger.info(f"Evaluation {evaluation_id} was cancelled by platform - this is normal")
        raise  # Re-raise CancelledError so it can be handled by the websocket app
    except Exception as e:
        logger.error(f"Error during evaluation: {e}", exc_info=True)
        errored = True
    finally:
        sandbox_manager.cleanup_all_sandboxes()
        global_status_running = False
