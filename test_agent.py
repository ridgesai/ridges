import os
import json
import click
import asyncio
import pathlib
import traceback
import utils.logger as logger

from uuid import uuid4
from datetime import datetime
from typing import List, Optional
from models.problem import ProblemTestResultStatus
from evaluator.models import EvaluationRunException
from evaluator.sandbox.sandbox_manager import SandboxManager
from evaluator.problem_suites.problem_suite import ProblemSuite
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunErrorCode
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite



TEST_AGENT_RESULTS_DIR = pathlib.Path(__file__).parent / "test_agent_results"



evaluation_id = uuid4()



global inference_gateway_url
global agent_code
global running_agent_timeout_seconds
global running_eval_timeout_seconds
global include_solutions



class LocalEvaluationRun(EvaluationRun):
    agent_logs: Optional[str] = None
    eval_logs: Optional[str] = None



async def run_local_evaluation_run(sandbox_manager: SandboxManager, problem_suites: List[ProblemSuite], problem_name: str):
    evaluation_run = LocalEvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=evaluation_id,

        problem_name=problem_name,

        status=EvaluationRunStatus.pending,

        patch=None,
        test_results=None,

        error_code=None,
        error_message=None,

        created_at=datetime.now()
    )

    try:
        problem_suite = next((suite for suite in problem_suites if suite.has_problem_name(problem_name)), None)

        if problem_suite is None:
            logger.error(f"[{problem_name}] The problem '{problem_name}' was not found")

            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM,
                f"The problem '{problem_name}' was not found"
            )

        problem = problem_suite.get_problem(problem_name)

        evaluation_run.status = EvaluationRunStatus.initializing_agent
        evaluation_run.started_initializing_agent_at = datetime.now()

        logger.info(f"[{problem_name}] Initializing agent...")
        agent_sandbox = await asyncio.to_thread(
            problem_suite.initialize_agent_sandbox,
            sandbox_manager, 
            problem, 
            evaluation_run.evaluation_run_id,
            agent_code,
            include_solution=include_solutions
        )
        logger.info(f"[{problem_name}] Finished initializing agent")

        evaluation_run.status = EvaluationRunStatus.running_agent
        evaluation_run.started_running_agent_at = datetime.now()

        logger.info(f"[{problem_name}] Running agent...")
        patch, agent_logs = await asyncio.to_thread(
            problem_suite.run_agent_sandbox,
            sandbox_manager,
            agent_sandbox,
            timeout_seconds=running_agent_timeout_seconds
        )
        logger.info(f"[{problem_name}] Finished running agent: {len(patch.splitlines())} line(s) of patch, {len(agent_logs.splitlines())} line(s) of agent logs")

        evaluation_run.patch = patch
        evaluation_run.agent_logs = agent_logs

        evaluation_run.status = EvaluationRunStatus.initializing_eval
        evaluation_run.started_initializing_eval_at = datetime.now()

        logger.info(f"[{problem_name}] Initializing evaluation...")
        eval_sandbox = await asyncio.to_thread(
            problem_suite.initialize_eval_sandbox,
            sandbox_manager,
            problem,
            evaluation_run.evaluation_run_id,
            patch
        )
        logger.info(f"[{problem_name}] Finished initializing evaluation")

        evaluation_run.status = EvaluationRunStatus.running_eval
        evaluation_run.started_running_eval_at = datetime.now()

        logger.info(f"[{problem_name}] Running evaluation...")
        test_results, eval_logs = await asyncio.to_thread(
            problem_suite.run_eval_sandbox,
            sandbox_manager,
            eval_sandbox,
            timeout_seconds=running_eval_timeout_seconds
        )
        num_passed = sum(1 for test in test_results if test.status == ProblemTestResultStatus.PASS)
        num_failed = sum(1 for test in test_results if test.status == ProblemTestResultStatus.FAIL)
        num_skipped = sum(1 for test in test_results if test.status == ProblemTestResultStatus.SKIP)
        if num_failed > 0:
            logger.error(f"[{problem_name}] Finished running evaluation: {num_passed} passed, {num_failed} failed, {num_skipped} skipped, {len(eval_logs.splitlines())} line(s) of eval logs")
        else:
            logger.info(f"[{problem_name}] Finished running evaluation: {num_passed} passed, {num_failed} failed, {num_skipped} skipped, {len(eval_logs.splitlines())} line(s) of eval logs")

        evaluation_run.test_results = test_results
        evaluation_run.eval_logs = eval_logs

        evaluation_run.status = EvaluationRunStatus.finished
        evaluation_run.finished_or_errored_at = datetime.now()

    except EvaluationRunException as e:
        evaluation_run.error_code = e.error_code
        evaluation_run.error_message = e.error_message

        evaluation_run.status = EvaluationRunStatus.error
        evaluation_run.finished_or_errored_at = datetime.now()

        logger.error(f"[{problem_name}] Errored: {e.error_code.get_error_message()}: {e.error_message}")

    except Exception as e:
        evaluation_run.error_code = EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
        evaluation_run.error_message = f"{EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"

        evaluation_run.status = EvaluationRunStatus.error
        evaluation_run.finished_or_errored_at = datetime.now()

        logger.error(f"[{problem_name}] Errored: {e.error_code.get_error_message()}: {e.error_message}")



    test_agent_result_dir = TEST_AGENT_RESULTS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}__{evaluation_run.evaluation_id}" / f"{problem_name}__{evaluation_run.evaluation_run_id}"

    logger.info(f"[{problem_name}] Saving results to {test_agent_result_dir}...")
    
    os.makedirs(test_agent_result_dir, exist_ok=True)

    with open(test_agent_result_dir / "evaluation_run.json", "w") as f:
        f.write(evaluation_run.model_dump_json(exclude={"agent_logs", "eval_logs"}))

    if evaluation_run.agent_logs is not None:
        with open(test_agent_result_dir / "agent_logs.txt", "w") as f:
            f.write(evaluation_run.agent_logs)

    if evaluation_run.eval_logs is not None:
        with open(test_agent_result_dir / "eval_logs.txt", "w") as f:
            f.write(evaluation_run.eval_logs)

    logger.info(f"[{problem_name}] Saved results to {test_agent_result_dir}...")



    return evaluation_run



async def run_problems(agent_code: str, problem_names: List[str]):
    os.makedirs(TEST_AGENT_RESULTS_DIR, exist_ok=True)


    
    sandbox_manager = SandboxManager(inference_gateway_url)

    datasets_path = pathlib.Path(__file__).parent / "evaluator" / "datasets"

    polyglot_suite = PolyglotSuite(datasets_path / "polyglot")
    swebench_verified_suite = SWEBenchVerifiedSuite(datasets_path / "swebench_verified")

    swebench_verified_suite.prebuild_problem_images(problem_names)

    tasks = []

    for problem_name in problem_names:
        tasks.append(asyncio.create_task(run_local_evaluation_run(sandbox_manager, [polyglot_suite, swebench_verified_suite], problem_name)))

    await asyncio.gather(*tasks)



@click.group()
@click.option("--inference-url", required=True, type=str, help="The inference gateway URL (e.g., http://192.168.0.1:1234)")
@click.option("--agent-path", required=True, type=str, help="The path to the agent file (e.g., ~/agents/agent.py)")
@click.option("--agent-timeout", default=2400, type=int, help="The timeout in seconds for running the agent, in seconds")
@click.option("--eval-timeout", default=600, type=int, help="The timeout in seconds for running the evaluation, in seconds")
@click.option("--include-solutions", "_include_solutions", is_flag=True, help="Whether or not to include solutions in the evaluation")
def cli(inference_url: str, agent_path: str, agent_timeout: int, eval_timeout: int, _include_solutions: bool):
    global inference_gateway_url
    global agent_code
    global running_agent_timeout_seconds
    global running_eval_timeout_seconds
    global include_solutions

    inference_gateway_url = inference_url
    with open(agent_path, "r") as f:
        agent_code = f.read()
    running_agent_timeout_seconds = agent_timeout
    running_eval_timeout_seconds = eval_timeout
    include_solutions = _include_solutions

    if include_solutions:
        logger.warning("Including Solutions!")



@cli.command()
@click.argument("problem_name", required=True, type=str)
def test_problem(problem_name: str):
    asyncio.run(run_problems(agent_code, [problem_name]))



with open(pathlib.Path(__file__).parent / "test_agent_problem_sets.json", "r") as f:
    problem_sets = json.load(f)

@cli.command()
@click.argument("problem_set_name", required=True, type=click.Choice(list(problem_sets.keys())))
def test_problem_set(problem_set_name: str):
    asyncio.run(run_problems(agent_code, problem_sets[problem_set_name]))



if __name__ == '__main__':
    cli()