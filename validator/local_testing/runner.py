"""
Local evaluation runner for testing agents without database infrastructure.

This module orchestrates:
- Loading SWE-bench problems
- Running evaluations on multiple problems
- Collecting and summarizing results
"""

import asyncio
import time
from typing import Dict, Any, List
from pathlib import Path

from swebench.harness.run_evaluation import load_swebench_dataset
from validator.local_testing.problem_instances import EASY_INSTANCES, SCREENER_INSTANCES, TEST_SCREENER_INSTANCES
from validator.sandbox.schema import SwebenchProblem
from validator.local_testing.local_manager import LocalSandboxManager
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def run_local_evaluations(
    agent_file: str,
    num_problems: int,
    timeout: int,
    problem_set: str,
    manager: LocalSandboxManager
) -> Dict[str, Any]:
    """Run local evaluations on selected problems"""
    
    # Load problems
    problems = load_local_problems(problem_set, num_problems)
    
    results = []
    start_time = time.time()
    
    for i, problem in enumerate(problems):
        print(f"\n[{i+1}/{len(problems)}] Testing: {problem.instance_id}")
        
        problem_start = time.time()
        
        try:
            # Create sandbox
            sandbox = await manager.create_sandbox(problem, Path(agent_file))
            
            # Run evaluation with timeout
            result = await asyncio.wait_for(
                run_single_evaluation(sandbox, problem),
                timeout=timeout
            )
            
            result['duration'] = time.time() - problem_start
            results.append(result)
            
        except asyncio.TimeoutError:
            results.append({
                'instance_id': problem.instance_id,
                'status': 'TIMEOUT',
                'solved': False,
                'error': f'Evaluation timed out after {timeout}s',
                'duration': timeout,
                'patch_generated': False,
                'patch_length': 0
            })
        except Exception as e:
            results.append({
                'instance_id': problem.instance_id,
                'status': 'ERROR',
                'solved': False,
                'error': str(e),
                'duration': time.time() - problem_start,
                'patch_generated': False,
                'patch_length': 0
            })
    
    # Generate summary
    summary = generate_summary(results)
    
    return {
        'results': results,
        'summary': summary,
        'total_duration': time.time() - start_time
    }

async def run_single_evaluation(sandbox, problem: SwebenchProblem) -> Dict[str, Any]:
    """Run evaluation on a single problem"""
    
    try:
        # Run the sandbox (this does patch generation + evaluation)
        await sandbox.run()
        
        # Extract results
        evaluation_run = sandbox.evaluation_run
        
        return {
            'instance_id': problem.instance_id,
            'status': 'SOLVED' if evaluation_run.solved else 'FAILED',
            'solved': evaluation_run.solved or False,
            'error': evaluation_run.error,
            'patch_generated': bool(evaluation_run.response),
            'patch_length': len(evaluation_run.response) if evaluation_run.response else 0,
            'fail_to_pass': evaluation_run.fail_to_pass_success,
            'pass_to_pass': evaluation_run.pass_to_pass_success,
        }
        
    except Exception as e:
        return {
            'instance_id': problem.instance_id,
            'status': 'ERROR',
            'solved': False,
            'error': str(e),
            'patch_generated': False,
            'patch_length': 0
        }

def load_local_problems(problem_set: str, num_problems: int) -> List[SwebenchProblem]:
    """Load problems for local testing"""
    
    # Select problem instances
    if problem_set == "screener":
        instances = TEST_SCREENER_INSTANCES  # Use smaller subset for local testing
    elif problem_set == "easy":
        instances = EASY_INSTANCES
    else:
        instances = TEST_SCREENER_INSTANCES  # Default fallback
    
    # Take only the requested number
    selected_instances = instances[:num_problems]
    
    # Load from SWE-bench dataset
    swebench_problems = load_swebench_dataset(
        "SWE-bench/SWE-bench_Verified", 
        "test", 
        selected_instances
    )
    
    # Convert to our problem format
    problems = []
    for instance in swebench_problems:
        problem = SwebenchProblem(
            instance_id=instance["instance_id"],
            problem_statement=instance["problem_statement"],
            repo=instance["repo"],
            base_commit=instance["base_commit"],
        )
        problems.append(problem)
    
    return problems

def generate_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate summary statistics"""
    
    total_count = len(results)
    solved_count = sum(1 for r in results if r['solved'])
    patches_generated = sum(1 for r in results if r.get('patch_generated', False))
    
    durations = [r['duration'] for r in results]
    avg_time = sum(durations) / len(durations) if durations else 0
    
    return {
        'total_count': total_count,
        'solved_count': solved_count,
        'success_rate': (solved_count / total_count * 100) if total_count > 0 else 0,
        'patches_generated': patches_generated,
        'avg_time': avg_time,
        'total_time': sum(durations)
    } 